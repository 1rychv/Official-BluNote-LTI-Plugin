from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import datetime


class LTIUser(BaseModel):
    platform_user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    roles: List[str] = []
    platform_issuer: str


class LTICourse(BaseModel):
    course_id: str
    title: Optional[str] = None
    label: Optional[str] = None


class LTIResourceLink(BaseModel):
    id: str
    title: Optional[str] = None
    description: Optional[str] = None


class LTIServices(BaseModel):
    nrps_url: Optional[str] = None
    ags_url: Optional[str] = None
    ags_lineitem_url: Optional[str] = None


class LTILaunch(BaseModel):
    launch_id: str
    platform_issuer: str
    deployment_id: Optional[str] = None
    user: LTIUser
    course: LTICourse
    resource_link: LTIResourceLink
    services: LTIServices
    message_type: str
    lti_version: str
    raw_claims: Dict[str, Any]
    timestamp: datetime


class LTIDeepLinkingSelection(BaseModel):
    activity_type: str = "confusion_tracker"
    title: Optional[str] = None
    custom_parameters: Optional[Dict[str, Any]] = None


class SessionClaims(BaseModel):
    jti: str
    sub: str
    iss: str = "BluNote"
    aud: str = "BluNote-frontend"
    exp: int
    iat: int
    nbf: int
    course_id: str
    course_title: Optional[str] = None
    user_id: str
    user_name: str
    user_email: Optional[str] = None
    roles: List[str] = []
    is_instructor: bool = False
    is_student: bool = False
    platform_issuer: str
    deployment_id: Optional[str] = None
    nrps_url: Optional[str] = None
    ags_url: Optional[str] = None