/**
 * WebSocket service for real-time communication with authentication
 */

import { io } from 'socket.io-client';
import authService from './auth';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:4000';

class SocketService {
  constructor() {
    this.socket = null;
    this.connected = false;
    this.listeners = new Map();
  }

  /**
   * Connect to WebSocket server with authentication
   */
  connect() {
    if (this.socket && this.connected) {
      return this.socket;
    }

    // Ensure we have authentication
    if (!authService.isAuthenticated()) {
      console.error('Cannot connect socket: not authenticated');
      return null;
    }

    this.socket = io(API_BASE, {
      auth: authService.getSocketAuth(),
      transports: ['websocket'],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000
    });

    this.setupEventHandlers();
    return this.socket;
  }

  /**
   * Setup WebSocket event handlers
   */
  setupEventHandlers() {
    this.socket.on('connect', () => {
      console.log('WebSocket connected');
      this.connected = true;
      this.emit('connection_status', { connected: true });
    });

    this.socket.on('disconnect', (reason) => {
      console.log('WebSocket disconnected:', reason);
      this.connected = false;
      this.emit('connection_status', { connected: false, reason });
    });

    this.socket.on('connect_error', (error) => {
      console.error('WebSocket connection error:', error);

      // If authentication failed, handle it
      if (error.message && error.message.includes('auth')) {
        authService.handleAuthError();
      }

      this.emit('connection_error', { error: error.message });
    });

    // Handle metrics updates
    this.socket.on('metrics', (data) => {
      this.emit('metrics', data);
    });

    // Handle confusion triggers
    this.socket.on('trigger', (data) => {
      this.emit('trigger', data);
    });

    // Handle tutoring content
    this.socket.on('tutoring', (data) => {
      this.emit('tutoring', data);
    });

    // Handle presence updates
    this.socket.on('presence', (data) => {
      this.emit('presence', data);
    });

    // Handle authentication success
    this.socket.on('connected', (data) => {
      console.log('WebSocket authenticated:', data);
      this.emit('authenticated', data);
    });
  }

  /**
   * Disconnect from WebSocket server
   */
  disconnect() {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
      this.connected = false;
    }
  }

  /**
   * Report confusion
   */
  async reportConfusion() {
    if (!this.isConnected()) {
      throw new Error('Not connected to server');
    }

    return new Promise((resolve, reject) => {
      this.socket.emit('confused', {}, (response) => {
        if (response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  }

  /**
   * Send presence heartbeat
   */
  sendPresence() {
    if (!this.isConnected()) return;

    this.socket.emit('presence', {}, (response) => {
      if (response.error) {
        console.error('Presence update failed:', response.error);
      }
    });
  }

  /**
   * Get current metrics
   */
  async getMetrics() {
    if (!this.isConnected()) {
      throw new Error('Not connected to server');
    }

    return new Promise((resolve, reject) => {
      this.socket.emit('get_metrics', {}, (response) => {
        if (response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response.metrics);
        }
      });
    });
  }

  /**
   * Clear confusion (instructor only)
   */
  async clearConfusion() {
    if (!this.isConnected()) {
      throw new Error('Not connected to server');
    }

    return new Promise((resolve, reject) => {
      this.socket.emit('clear_confusion', {}, (response) => {
        if (response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  }

  /**
   * Send tutoring content (instructor only)
   */
  async sendTutoring(content) {
    if (!this.isConnected()) {
      throw new Error('Not connected to server');
    }

    return new Promise((resolve, reject) => {
      this.socket.emit('send_tutoring', { content }, (response) => {
        if (response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  }

  /**
   * Check if connected
   */
  isConnected() {
    return this.socket && this.connected;
  }

  /**
   * Add event listener
   */
  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);
  }

  /**
   * Remove event listener
   */
  off(event, callback) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).delete(callback);
    }
  }

  /**
   * Emit event to listeners
   */
  emit(event, data) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).forEach(callback => {
        try {
          callback(data);
        } catch (error) {
          console.error('Error in event listener:', error);
        }
      });
    }
  }

  /**
   * Start presence heartbeat
   */
  startPresence() {
    this.presenceInterval = setInterval(() => {
      this.sendPresence();
    }, 30000); // Every 30 seconds
  }

  /**
   * Stop presence heartbeat
   */
  stopPresence() {
    if (this.presenceInterval) {
      clearInterval(this.presenceInterval);
      this.presenceInterval = null;
    }
  }
}

// Export singleton instance
const socketService = new SocketService();
export default socketService;