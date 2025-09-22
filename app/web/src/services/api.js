/**
 * API service for making authenticated requests
 */

import authService from './auth';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:4000';

class ApiService {
  constructor() {
    this.baseUrl = API_BASE;
  }

  /**
   * Make authenticated request
   */
  async request(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...authService.getAuthHeader(),
      ...options.headers
    };

    try {
      const response = await fetch(`${this.baseUrl}${url}`, {
        ...options,
        headers
      });

      // Handle auth errors
      if (response.status === 401) {
        authService.handleAuthError();
        throw new Error('Authentication failed');
      }

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(error.detail || 'Request failed');
      }

      return response.json();
    } catch (error) {
      console.error('API request failed:', error);
      throw error;
    }
  }

  /**
   * GET request
   */
  async get(url) {
    return this.request(url, { method: 'GET' });
  }

  /**
   * POST request
   */
  async post(url, data) {
    return this.request(url, {
      method: 'POST',
      body: JSON.stringify(data)
    });
  }

  /**
   * Report confusion
   */
  async reportConfusion(courseId, userId) {
    return this.post('/api/confused', {
      course_id: courseId,
      user_id: userId
    });
  }

  /**
   * Get course metrics
   */
  async getMetrics(courseId) {
    return this.get(`/api/metrics/${courseId}`);
  }

  /**
   * Clear confusion (instructor only)
   */
  async clearConfusion(courseId) {
    return this.post('/api/clear_confusion', {
      course_id: courseId
    });
  }

  /**
   * Send tutoring content (instructor only)
   */
  async sendTutoring(courseId, content) {
    return this.post('/api/tutoring', {
      course_id: courseId,
      content
    });
  }

  /**
   * Update roster (instructor only)
   */
  async updateRoster(courseId, count) {
    return this.post('/api/roster', {
      course_id: courseId,
      count
    });
  }
}

// Export singleton instance
const apiService = new ApiService();
export default apiService;