'use strict';

(() => {
    const API_BASE = window.location.protocol === 'https:'
        ? window.location.origin
        : window.location.protocol === 'file:'
            ? 'http://localhost:8000'
            : `${window.location.protocol}//${window.location.hostname}:8000`;
    const TOKEN_KEY = 'qr-ocr-access-token';
    const USER_KEY = 'qr-ocr-current-user';

    function token() {
        return sessionStorage.getItem(TOKEN_KEY);
    }

    function user() {
        const serialized = sessionStorage.getItem(USER_KEY);
        if (!serialized) return null;
        try {
            return JSON.parse(serialized);
        } catch {
            logout();
            return null;
        }
    }

    function headers(existing = {}) {
        const accessToken = token();
        return accessToken
            ? { ...existing, Authorization: `Bearer ${accessToken}` }
            : existing;
    }

    async function login(username, password) {
        window.dispatchEvent(new Event('app-auth-login-start'));
        try {
            const response = await fetch(`${API_BASE}/api/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
            sessionStorage.setItem(TOKEN_KEY, result.access_token);
            sessionStorage.setItem(USER_KEY, JSON.stringify(result.user));
            window.dispatchEvent(new Event('app-auth-login'));
            return result.user;
        } catch (error) {
            window.dispatchEvent(new Event('app-auth-login-failure'));
            throw error;
        }
    }

    function logout() {
        sessionStorage.removeItem(TOKEN_KEY);
        sessionStorage.removeItem(USER_KEY);
        window.dispatchEvent(new Event('app-auth-logout'));
    }

    window.AppAuth = { API_BASE, token, user, headers, login, logout };
})();
