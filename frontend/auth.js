"use strict";

/*
 * Authentification QR-OCR
 *
 * Le frontend et l'API sont servis derrière le même reverse proxy.
 * Aucun accès direct à FastAPI :8000 depuis le navigateur.
 *
 * Exemple :
 *   /api/auth/login
 *        ↓
 *   Nginx
 *        ↓
 *   api:8000 (réseau Docker privé)
 */

(() => {

const API_BASE = "";
const AUTH_TOKEN_KEY = "qr_ocr_access_token";
const AUTH_USER_KEY = "qr_ocr_user";

/* ============================================================
 * Stockage session
 * ============================================================ */

function getAuthToken() {
    return sessionStorage.getItem(AUTH_TOKEN_KEY);
}


function getStoredUser() {
    const rawUser = sessionStorage.getItem(AUTH_USER_KEY);

    if (!rawUser) {
        return null;
    }

    try {
        return JSON.parse(rawUser);
    } catch {
        sessionStorage.removeItem(AUTH_USER_KEY);
        return null;
    }
}


function saveAuthSession(token, user) {
    if (!token) {
        throw new Error("Jeton d'authentification manquant.");
    }

    sessionStorage.setItem(
        AUTH_TOKEN_KEY,
        token
    );

    if (user) {
        sessionStorage.setItem(
            AUTH_USER_KEY,
            JSON.stringify(user)
        );
    } else {
        sessionStorage.removeItem(AUTH_USER_KEY);
    }
}


function clearAuthSession() {
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    sessionStorage.removeItem(AUTH_USER_KEY);
}


/* ============================================================
 * Headers API
 * ============================================================ */

function authHeaders(extraHeaders = {}) {
    const token = getAuthToken();

    const headers = {
        ...extraHeaders
    };

    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }

    return headers;
}


function jsonAuthHeaders(extraHeaders = {}) {
    return authHeaders({
        "Content-Type": "application/json",
        ...extraHeaders
    });
}


/* ============================================================
 * Appel API générique
 * ============================================================ */

async function apiFetch(path, options = {}) {

    if (!path.startsWith("/")) {
        path = `/${path}`;
    }

    const url = `${API_BASE}${path}`;

    const headers = authHeaders(
        options.headers || {}
    );

    let response;

    try {

        response = await fetch(url, {
            ...options,
            headers
        });

    } catch (error) {

        console.error(
            "Erreur réseau API :",
            error
        );

        throw new Error(
            "API inaccessible. Vérifiez que le service backend est disponible."
        );
    }

    /*
     * Token invalide ou expiré.
     */
    if (
        response.status === 401 &&
        path !== "/api/auth/login"
    ) {

        clearAuthSession();

        if (
            !window.location.pathname.endsWith("login.html") &&
            !window.location.pathname.endsWith("/")
        ) {
            window.location.href = "login.html";
        }
    }

    return response;
}


/* ============================================================
 * Lecture réponse d'erreur FastAPI
 * ============================================================ */

async function readApiError(
    response,
    defaultMessage = "Une erreur est survenue."
) {

    try {

        const data = await response.json();

        if (typeof data.detail === "string") {
            return data.detail;
        }

        if (Array.isArray(data.detail)) {

            return data.detail
                .map((item) => {
                    if (item.msg) {
                        return item.msg;
                    }

                    return JSON.stringify(item);
                })
                .join("\n");
        }

        if (data.message) {
            return data.message;
        }

    } catch {
        // La réponse n'était pas du JSON.
    }

    return defaultMessage;
}


/* ============================================================
 * Connexion
 * ============================================================ */

async function login(username, password) {
    window.dispatchEvent(new Event("app-auth-login-start"));

    try {

        const normalizedUsername =
            String(username || "").trim();

        if (!normalizedUsername) {
            throw new Error(
                "Le nom d'utilisateur est requis."
            );
        }

        if (!password) {
            throw new Error(
                "Le mot de passe est requis."
            );
        }

        let response;

        try {

            response = await fetch(
                `${API_BASE}/api/auth/login`,
                {
                    method: "POST",

                    headers: {
                        "Content-Type": "application/json"
                    },

                    body: JSON.stringify({
                        username: normalizedUsername,
                        password: password
                    })
                }
            );

        } catch (error) {

            console.error(
                "Erreur de connexion à l'API :",
                error
            );

            throw new Error(
                "Connexion impossible : le service backend est inaccessible."
            );
        }

        if (!response.ok) {

            const message = await readApiError(
                response,
                "Identifiants incorrects."
            );

            throw new Error(message);
        }

        const data = await response.json();

        if (!data.access_token) {
            throw new Error(
                "La réponse de connexion ne contient aucun jeton d'accès."
            );
        }

        if (!data.user) {
            throw new Error(
                "La réponse de connexion ne contient aucun utilisateur."
            );
        }

        saveAuthSession(
            data.access_token,
            data.user
        );

        window.dispatchEvent(new Event("app-auth-login"));
        return data.user;
    } catch (error) {
        window.dispatchEvent(new Event("app-auth-login-failure"));
        throw error;
    }
}


/* ============================================================
 * Utilisateur actuel
 * ============================================================ */

async function loadCurrentUser() {

    const token = getAuthToken();

    if (!token) {
        return null;
    }

    const response = await apiFetch(
        "/api/auth/me"
    );

    if (!response.ok) {

        if (response.status === 401) {
            clearAuthSession();
            return null;
        }

        const message = await readApiError(
            response,
            "Impossible de récupérer l'utilisateur connecté."
        );

        throw new Error(message);
    }

    const user = await response.json();

    sessionStorage.setItem(
        AUTH_USER_KEY,
        JSON.stringify(user)
    );

    return user;
}


/* ============================================================
 * Déconnexion
 * ============================================================ */

function logout(redirectTo = null) {

    clearAuthSession();
    window.dispatchEvent(new Event("app-auth-logout"));

    if (redirectTo) {
        window.location.href = redirectTo;
    }
}


/* ============================================================
 * Protection des pages
 * ============================================================ */

async function requireAuth(options = {}) {

    const {
        roles = null,
        loginPage = "login.html"
    } = options;

    const token = getAuthToken();

    if (!token) {

        window.location.href = loginPage;

        return null;
    }

    const user = await loadCurrentUser();

    if (!user) {
        window.location.href = loginPage;
        return null;
    }

    if (
        Array.isArray(roles) &&
        roles.length > 0 &&
        !roles.includes(user.role)
    ) {
        throw new Error(
            "Votre compte ne possède pas les droits requis."
        );
    }

    return user;
}

window.AppAuth = {
    API_BASE,
    token: getAuthToken,
    user: getStoredUser,
    headers: authHeaders,
    login,
    logout,
    apiFetch,
    requireAuth
};
})();