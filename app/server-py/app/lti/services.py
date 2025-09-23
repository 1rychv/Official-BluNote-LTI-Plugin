"""
LTI Advantage Services: NRPS and AGS integration
Handles roster sync and grade passback functionality
"""
import time
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import httpx
from jose import jwt
import asyncio
from uuid import uuid4

from .config import lti_config
from .auth import lti_auth
from .cache import token_cache
from ..database.connection import get_db_connection, get_redis_connection


class ServiceTokenManager:
    """Manages OAuth 2.0 tokens for LTI Advantage service calls"""

    def __init__(self):
        self.token_cache: Dict[str, Dict[str, Any]] = {}

    async def get_service_token(
        self,
        platform_issuer: str,
        scopes: List[str],
        client_id: Optional[str] = None
    ) -> str:
        """
        Get or refresh an OAuth 2.0 token for service calls

        Args:
            platform_issuer: The platform's issuer URL
            scopes: List of required scopes (e.g., ['https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly'])
            client_id: Optional client ID override

        Returns:
            Valid access token string
        """
        cache_key = f"service_token:{platform_issuer}:{':'.join(sorted(scopes))}"

        # Check cache
        cached_token = await token_cache.get(cache_key)
        if cached_token:
            token_data = json.loads(cached_token)
            if token_data['expires_at'] > time.time() + 60:  # 1 minute buffer
                return token_data['access_token']

        # Fetch new token
        token_data = await self._fetch_service_token(platform_issuer, scopes, client_id)

        # Cache with TTL
        expires_in = token_data.get('expires_in', 3600)
        token_data['expires_at'] = time.time() + expires_in

        await token_cache.set(
            cache_key,
            json.dumps(token_data),
            ttl=expires_in - 60  # Cache slightly less than actual expiry
        )

        return token_data['access_token']

    async def _fetch_service_token(
        self,
        platform_issuer: str,
        scopes: List[str],
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch a new service token from the platform"""

        # Get platform's token endpoint
        platform_config = await lti_auth.fetch_platform_config(platform_issuer)
        token_endpoint = platform_config.get('token_endpoint')

        if not token_endpoint:
            raise ValueError(f"No token endpoint found for platform: {platform_issuer}")

        # Create client assertion (JWT)
        now = int(time.time())
        client_assertion = {
            'iss': client_id or lti_config.PLATFORM_CLIENT_ID,
            'sub': client_id or lti_config.PLATFORM_CLIENT_ID,
            'aud': [platform_issuer, token_endpoint],
            'iat': now,
            'exp': now + 60,
            'jti': str(uuid4())
        }

        assertion_jwt = lti_auth.lti_signer.sign(client_assertion)

        # Request token
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_endpoint,
                data={
                    'grant_type': 'client_credentials',
                    'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                    'client_assertion': assertion_jwt,
                    'scope': ' '.join(scopes)
                }
            )
            response.raise_for_status()
            return response.json()


class NRPSService:
    """Names and Role Provisioning Service integration"""

    NRPS_SCOPE = 'https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly'

    def __init__(self, token_manager: ServiceTokenManager):
        self.token_manager = token_manager

    async def sync_course_members(
        self,
        course_id: str,
        nrps_url: str,
        platform_issuer: str,
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synchronize course membership from the platform

        Args:
            course_id: Internal course ID
            nrps_url: NRPS context memberships URL from launch
            platform_issuer: Platform issuer URL
            client_id: Optional client ID

        Returns:
            Dict with sync results
        """
        if not nrps_url:
            return {'success': False, 'error': 'No NRPS URL provided'}

        try:
            # Get service token
            token = await self.token_manager.get_service_token(
                platform_issuer,
                [self.NRPS_SCOPE],
                client_id
            )

            # Fetch members
            members = await self._fetch_all_members(nrps_url, token)

            # Store in database
            stored_count = await self._store_members(course_id, members)

            # Update roster count in Redis
            redis = await get_redis_connection()
            await redis.setex(
                f"roster:{course_id}",
                300,  # 5 minute TTL
                str(len(members))
            )

            return {
                'success': True,
                'total_members': len(members),
                'stored_count': stored_count,
                'timestamp': datetime.utcnow().isoformat()
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }

    async def _fetch_all_members(
        self,
        nrps_url: str,
        token: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch all members, handling pagination"""
        all_members = []
        next_url = nrps_url

        async with httpx.AsyncClient(timeout=30) as client:
            while next_url:
                # Add limit parameter
                separator = '&' if '?' in next_url else '?'
                url = f"{next_url}{separator}limit={limit}"

                response = await client.get(
                    url,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'Accept': 'application/vnd.ims.lti-nrps.v2.membershipcontainer+json'
                    }
                )
                response.raise_for_status()

                data = response.json()
                members = data.get('members', [])
                all_members.extend(members)

                # Check for next page
                next_url = None
                link_header = response.headers.get('Link')
                if link_header:
                    # Parse Link header for rel="next"
                    links = self._parse_link_header(link_header)
                    next_url = links.get('next')

        return all_members

    def _parse_link_header(self, link_header: str) -> Dict[str, str]:
        """Parse HTTP Link header"""
        links = {}
        for link in link_header.split(','):
            parts = link.split(';')
            if len(parts) == 2:
                url = parts[0].strip('<> ')
                rel = parts[1].strip()
                if rel.startswith('rel='):
                    rel_value = rel[4:].strip('"')
                    links[rel_value] = url
        return links

    async def _store_members(
        self,
        course_id: str,
        members: List[Dict[str, Any]]
    ) -> int:
        """Store members in database"""
        conn = await get_db_connection()
        stored = 0

        try:
            for member in members:
                # Extract member info
                user_id = member.get('user_id', '')
                if not user_id:
                    continue

                member_data = {
                    'course_id': course_id,
                    'user_id': user_id,
                    'name': member.get('name', ''),
                    'given_name': member.get('given_name', ''),
                    'family_name': member.get('family_name', ''),
                    'email': member.get('email', ''),
                    'roles': json.dumps(member.get('roles', [])),
                    'status': member.get('status', 'Active'),
                    'updated_at': datetime.utcnow()
                }

                # Upsert member
                await conn.execute("""
                    INSERT INTO course_members (
                        course_id, user_id, name, given_name, family_name,
                        email, roles, status, updated_at
                    ) VALUES (
                        :course_id, :user_id, :name, :given_name, :family_name,
                        :email, :roles, :status, :updated_at
                    )
                    ON CONFLICT (course_id, user_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        given_name = EXCLUDED.given_name,
                        family_name = EXCLUDED.family_name,
                        email = EXCLUDED.email,
                        roles = EXCLUDED.roles,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                """, member_data)

                stored += 1

        finally:
            await conn.close()

        return stored


class AGSService:
    """Assignment and Grade Services integration"""

    AGS_LINEITEM_SCOPE = 'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem'
    AGS_LINEITEM_READONLY_SCOPE = 'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem.readonly'
    AGS_SCORE_SCOPE = 'https://purl.imsglobal.org/spec/lti-ags/scope/score'
    AGS_RESULT_READONLY_SCOPE = 'https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly'

    def __init__(self, token_manager: ServiceTokenManager):
        self.token_manager = token_manager

    async def ensure_line_item(
        self,
        course_id: str,
        lineitems_url: str,
        platform_issuer: str,
        label: str = "BluNote Participation",
        score_maximum: float = 100.0,
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ensure a line item exists for grade passback

        Args:
            course_id: Internal course ID
            lineitems_url: AGS line items container URL
            platform_issuer: Platform issuer URL
            label: Line item label
            score_maximum: Maximum score
            client_id: Optional client ID

        Returns:
            Line item data
        """
        if not lineitems_url:
            raise ValueError("No AGS line items URL provided")

        # Get service token with necessary scopes
        token = await self.token_manager.get_service_token(
            platform_issuer,
            [self.AGS_LINEITEM_SCOPE, self.AGS_SCORE_SCOPE],
            client_id
        )

        # Check for existing line item
        existing = await self._find_line_item(lineitems_url, label, token)
        if existing:
            return existing

        # Create new line item
        return await self._create_line_item(
            lineitems_url,
            label,
            score_maximum,
            course_id,
            token
        )

    async def _find_line_item(
        self,
        lineitems_url: str,
        label: str,
        token: str
    ) -> Optional[Dict[str, Any]]:
        """Find existing line item by label"""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                lineitems_url,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/vnd.ims.lis.v2.lineitemcontainer+json'
                }
            )

            if response.status_code == 200:
                line_items = response.json()
                for item in line_items:
                    if item.get('label') == label:
                        return item

        return None

    async def _create_line_item(
        self,
        lineitems_url: str,
        label: str,
        score_maximum: float,
        resource_id: str,
        token: str
    ) -> Dict[str, Any]:
        """Create new line item"""
        line_item = {
            'scoreMaximum': score_maximum,
            'label': label,
            'resourceId': f"BluNote_{resource_id}",
            'tag': 'participation',
            'resourceLinkId': resource_id
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                lineitems_url,
                json=line_item,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/vnd.ims.lis.v2.lineitem+json',
                    'Accept': 'application/vnd.ims.lis.v2.lineitem+json'
                }
            )
            response.raise_for_status()
            return response.json()

    async def submit_score(
        self,
        user_id: str,
        line_item_url: str,
        platform_issuer: str,
        score: float,
        score_maximum: float = 100.0,
        comment: Optional[str] = None,
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Submit a score for a user

        Args:
            user_id: Platform user ID
            line_item_url: Direct line item URL
            platform_issuer: Platform issuer URL
            score: Actual score
            score_maximum: Maximum possible score
            comment: Optional comment
            client_id: Optional client ID

        Returns:
            Submission result
        """
        # Get service token
        token = await self.token_manager.get_service_token(
            platform_issuer,
            [self.AGS_SCORE_SCOPE],
            client_id
        )

        # Build score object
        score_data = {
            'userId': user_id,
            'scoreGiven': score,
            'scoreMaximum': score_maximum,
            'activityProgress': 'Completed',
            'gradingProgress': 'FullyGraded',
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        if comment:
            score_data['comment'] = comment

        # Submit score
        scores_url = f"{line_item_url}/scores"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                scores_url,
                json=score_data,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/vnd.ims.lis.v1.score+json'
                }
            )

            if response.status_code in [200, 201]:
                return {
                    'success': True,
                    'status_code': response.status_code,
                    'timestamp': datetime.utcnow().isoformat()
                }
            else:
                return {
                    'success': False,
                    'status_code': response.status_code,
                    'error': response.text,
                    'timestamp': datetime.utcnow().isoformat()
                }


class RosterSyncJob:
    """Background job for periodic roster synchronization"""

    def __init__(self, nrps_service: NRPSService):
        self.nrps_service = nrps_service
        self.running = False
        self.sync_interval = 1800  # 30 minutes

    async def start(self):
        """Start the background sync job"""
        if self.running:
            return

        self.running = True
        asyncio.create_task(self._sync_loop())

    async def stop(self):
        """Stop the background sync job"""
        self.running = False

    async def _sync_loop(self):
        """Main sync loop"""
        while self.running:
            try:
                await self._sync_all_courses()
            except Exception as e:
                print(f"Roster sync error: {e}")

            # Wait for next sync
            await asyncio.sleep(self.sync_interval)

    async def _sync_all_courses(self):
        """Sync roster for all active courses"""
        conn = await get_db_connection()

        try:
            # Get all courses with NRPS URLs
            result = await conn.fetch("""
                SELECT DISTINCT
                    c.id as course_id,
                    c.settings->>'nrps_url' as nrps_url,
                    c.settings->>'platform_issuer' as platform_issuer,
                    c.settings->>'client_id' as client_id
                FROM courses c
                WHERE
                    c.settings->>'nrps_url' IS NOT NULL
                    AND c.updated_at > NOW() - INTERVAL '7 days'
            """)

            # Sync each course
            for row in result:
                if row['nrps_url']:
                    try:
                        await self.nrps_service.sync_course_members(
                            course_id=row['course_id'],
                            nrps_url=row['nrps_url'],
                            platform_issuer=row['platform_issuer'],
                            client_id=row['client_id']
                        )
                    except Exception as e:
                        print(f"Failed to sync course {row['course_id']}: {e}")

                # Small delay between courses
                await asyncio.sleep(1)

        finally:
            await conn.close()


# Initialize services
token_manager = ServiceTokenManager()
nrps_service = NRPSService(token_manager)
ags_service = AGSService(token_manager)
roster_sync_job = RosterSyncJob(nrps_service)