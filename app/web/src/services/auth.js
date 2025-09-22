/**
 * Authentication service for handling JWT tokens and session management
 */

class AuthService {
  constructor() {
    this.token = null;
    this.claims = null;
    this.tokenKey = 'bluenote_token';
    this.claimsKey = 'bluenote_claims';

    // Load token from URL or storage on init
    this.initializeFromUrl();
  }

  /**
   * Initialize auth from URL parameters (LTI launch)
   */
  initializeFromUrl() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');

    if (token) {
      // Store token and parse claims
      this.setToken(token);

      // Clean URL to remove token
      window.history.replaceState({}, document.title, window.location.pathname);

      // Determine view based on URL params
      const view = urlParams.get('view');
      const courseId = urlParams.get('course');

      // Redirect to appropriate view
      if (view === 'dashboard') {
        window.location.href = `/instructor?courseId=${courseId}`;
      } else if (view === 'student') {
        window.location.href = `/student?courseId=${courseId}`;
      }
    } else {
      // Try to load from storage
      this.loadFromStorage();
    }
  }

  /**
   * Set and store JWT token
   */
  setToken(token) {
    this.token = token;
    sessionStorage.setItem(this.tokenKey, token);

    // Parse and store claims
    try {
      const payload = token.split('.')[1];
      const claims = JSON.parse(atob(payload));
      this.claims = claims;
      sessionStorage.setItem(this.claimsKey, JSON.stringify(claims));
    } catch (e) {
      console.error('Failed to parse JWT token:', e);
    }
  }

  /**
   * Load token from session storage
   */
  loadFromStorage() {
    const token = sessionStorage.getItem(this.tokenKey);
    if (token) {
      this.token = token;
      const claims = sessionStorage.getItem(this.claimsKey);
      if (claims) {
        try {
          this.claims = JSON.parse(claims);
        } catch (e) {
          console.error('Failed to parse stored claims:', e);
        }
      }
    }
  }

  /**
   * Get current token
   */
  getToken() {
    return this.token;
  }

  /**
   * Get authorization header
   */
  getAuthHeader() {
    if (!this.token) return {};
    return {
      'Authorization': `Bearer ${this.token}`
    };
  }

  /**
   * Get user claims
   */
  getClaims() {
    return this.claims;
  }

  /**
   * Get specific claim value
   */
  getClaim(key) {
    return this.claims ? this.claims[key] : null;
  }

  /**
   * Check if user is authenticated
   */
  isAuthenticated() {
    if (!this.token || !this.claims) return false;

    // Check if token is expired
    const exp = this.claims.exp;
    if (exp) {
      const now = Math.floor(Date.now() / 1000);
      if (now >= exp) {
        this.clearAuth();
        return false;
      }
    }

    return true;
  }

  /**
   * Check if user is instructor
   */
  isInstructor() {
    return this.claims?.is_instructor === true;
  }

  /**
   * Check if user is student
   */
  isStudent() {
    return this.claims?.is_student === true;
  }

  /**
   * Get user info
   */
  getUserInfo() {
    if (!this.claims) return null;

    return {
      userId: this.claims.user_id,
      userName: this.claims.user_name || 'Anonymous',
      userEmail: this.claims.user_email,
      courseId: this.claims.course_id,
      courseTitle: this.claims.course_title,
      isInstructor: this.claims.is_instructor,
      isStudent: this.claims.is_student
    };
  }

  /**
   * Get WebSocket auth data
   */
  getSocketAuth() {
    return {
      token: this.token
    };
  }

  /**
   * Clear authentication
   */
  clearAuth() {
    this.token = null;
    this.claims = null;
    sessionStorage.removeItem(this.tokenKey);
    sessionStorage.removeItem(this.claimsKey);
  }

  /**
   * Handle authentication error
   */
  handleAuthError() {
    this.clearAuth();
    alert('Session expired. Please relaunch from your LMS.');
    // In production, might redirect to an error page
  }
}

// Export singleton instance
const authService = new AuthService();
export default authService;