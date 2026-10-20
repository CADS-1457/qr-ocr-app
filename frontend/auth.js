"use strict";

(() => {

/*
 * QR-OCR - Gestion centralisée de l'authentification
 *
 * Architecture :
 *
 * Navigateur
 *     |
 *     | /api/...
 *     v
 * Nginx frontend
 *     |
 *     | Réseau Docker privé
 *     v
 * FastAPI api:8000
 *
 * Aucun appel direct au port 8000 n'est effectué par le navigateur.
 */


/* ============================================================
 * Configuration
 * ============================================================ */

const API_BASE = "";

const AUTH_TOKEN_KEY = "qr_ocr_access_token";
const AUTH_USER_KEY = "qr_ocr_user";
const AUTH_RETURN_URL_KEY = "qr_ocr_return_url";
const AUTH_EVENT_KEY = "qr_ocr_auth_event";

const LOGIN_PAGE = "/";
const DEFAULT_PAGE_AFTER_LOGIN = "/";

const AUTH_CHANNEL_NAME = "qr_ocr_auth_channel";

let authenticationPromise = null;
let loginRequiredInProgress = false;


/* ============================================================
 * Canal de communication entre onglets
 * ============================================================ */

const authChannel =
    "BroadcastChannel" in window
        ? new BroadcastChannel(AUTH_CHANNEL_NAME)
        : null;


/**
 * Envoie un événement d'authentification aux autres onglets.
 */
function broadcastAuthEvent(type, data = {}) {
    const event = {
        type,
        data,
        timestamp: Date.now()
    };

    /*
     * BroadcastChannel est la méthode directe lorsqu'elle est disponible.
     */
    if (authChannel) {
        authChannel.postMessage(event);
    }

    /*
     * L'écriture dans localStorage déclenche également l'événement
     * "storage" dans les autres onglets.
     */
    localStorage.setItem(
        AUTH_EVENT_KEY,
        JSON.stringify(event)
    );
}


/* ============================================================
 * Stockage de la session
 * ============================================================ */

/**
 * Retourne le jeton partagé entre tous les onglets.
 */
function getAuthToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY);
}


/**
 * Retourne l'utilisateur précédemment mémorisé.
 */
function getStoredUser() {
    const rawUser = localStorage.getItem(AUTH_USER_KEY);

    if (!rawUser) {
        return null;
    }

    try {
        return JSON.parse(rawUser);
    } catch (error) {
        console.warn(
            "Les données utilisateur stockées sont invalides.",
            error
        );

        localStorage.removeItem(AUTH_USER_KEY);

        return null;
    }
}


/**
 * Enregistre le jeton et l'utilisateur.
 */
function saveAuthSession(token, user) {
    if (
        typeof token !== "string" ||
        token.trim() === ""
    ) {
        throw new Error(
            "Le jeton d'authentification est manquant."
        );
    }

    localStorage.setItem(
        AUTH_TOKEN_KEY,
        token
    );

    if (user) {
        localStorage.setItem(
            AUTH_USER_KEY,
            JSON.stringify(user)
        );
    } else {
        localStorage.removeItem(AUTH_USER_KEY);
    }

    broadcastAuthEvent(
        "login",
        {
            user: user || null
        }
    );
}


/**
 * Supprime la session locale.
 */
function clearAuthSession({
    broadcast = true
} = {}) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);

    authenticationPromise = null;

    if (broadcast) {
        broadcastAuthEvent("logout");
    }
}


/* ============================================================
 * Gestion de la page de retour
 * ============================================================ */

/**
 * Retourne l'URL relative de la page actuelle.
 */
function getCurrentRelativeUrl() {
    return (
        window.location.pathname +
        window.location.search +
        window.location.hash
    );
}


/**
 * Indique si la page actuelle est la page de connexion.
 */
function isLoginPage() {
    const path = window.location.pathname;

    return (
        path === "/" ||
        path.endsWith("/index.html") ||
        path.endsWith("/login.html")
    );
}


/**
 * Vérifie qu'une URL de retour reste interne à l'application.
 */
function isSafeInternalUrl(url) {
    return (
        typeof url === "string" &&
        url.startsWith("/") &&
        !url.startsWith("//")
    );
}


/**
 * Mémorise la page protégée demandée.
 */
function rememberReturnUrl(url = getCurrentRelativeUrl()) {
    if (
        isLoginPage() ||
        !isSafeInternalUrl(url)
    ) {
        return;
    }

    localStorage.setItem(
        AUTH_RETURN_URL_KEY,
        url
    );
}


/**
 * Retourne la page mémorisée puis efface la valeur.
 */
function consumeReturnUrl() {
    const returnUrl = localStorage.getItem(
        AUTH_RETURN_URL_KEY
    );

    localStorage.removeItem(
        AUTH_RETURN_URL_KEY
    );

    if (!isSafeInternalUrl(returnUrl)) {
        return null;
    }

    return returnUrl;
}


/* ============================================================
 * Événements DOM utilisés par l'interface
 * ============================================================ */

/**
 * Déclenche un événement écoutable par index.html ou un composant modal.
 */
function dispatchAuthEvent(name, detail = {}) {
    window.dispatchEvent(
        new CustomEvent(
            name,
            {
                detail
            }
        )
    );
}


/**
 * Demande l'affichage du module de connexion.
 *
 * Si la page contient une fonction openLoginModal(), elle est utilisée.
 * Sinon, un événement qrocr:auth-required est déclenché.
 * En dernier recours, l'utilisateur est redirigé vers la page d'accueil.
 */
function showLoginModule({
    reason = "required",
    rememberCurrentPage = true,
    message = ""
} = {}) {
    if (rememberCurrentPage && !isLoginPage()) {
        rememberReturnUrl();
    }

    clearAuthSession({
        broadcast: false
    });

    dispatchAuthEvent(
        "qrocr:auth-required",
        {
            reason,
            message,
            returnUrl: localStorage.getItem(
                AUTH_RETURN_URL_KEY
            )
        }
    );

    /*
     * Compatibilité avec un module modal fourni par index.html.
     */
    if (typeof window.openLoginModal === "function") {
        window.openLoginModal({
            reason,
            message
        });

        return;
    }

    /*
     * Sur la page d'accueil, le formulaire de connexion peut être
     * affiché sans navigation supplémentaire.
     */
    if (isLoginPage()) {
        const loginModule =
            document.getElementById("loginModule") ||
            document.getElementById("loginPanel") ||
            document.getElementById("loginSection");

        if (loginModule) {
            loginModule.hidden = false;

            loginModule.classList.add(
                "is-visible"
            );
        }

        const usernameInput =
            document.getElementById("username");

        if (usernameInput) {
            usernameInput.focus();
        }

        return;
    }

    /*
     * Page protégée sans module local : retour vers l'accueil.
     */
    if (!loginRequiredInProgress) {
        loginRequiredInProgress = true;

        const target =
            `${LOGIN_PAGE}?login=${encodeURIComponent(reason)}`;

        window.location.replace(target);
    }
}


/* ============================================================
 * En-têtes HTTP
 * ============================================================ */

function authHeaders(extraHeaders = {}) {
    const headers = new Headers(extraHeaders);
    const token = getAuthToken();

    if (token) {
        headers.set(
            "Authorization",
            `Bearer ${token}`
        );
    }

    return headers;
}


function jsonAuthHeaders(extraHeaders = {}) {
    const headers = authHeaders(extraHeaders);

    if (!headers.has("Content-Type")) {
        headers.set(
            "Content-Type",
            "application/json"
        );
    }

    return headers;
}


/* ============================================================
 * Lecture des erreurs FastAPI
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
                    if (item && item.msg) {
                        return item.msg;
                    }

                    return JSON.stringify(item);
                })
                .join("\n");
        }

        if (typeof data.message === "string") {
            return data.message;
        }
    } catch {
        /*
         * La réponse ne contenait pas de JSON exploitable.
         */
    }

    return defaultMessage;
}


/* ============================================================
 * Appel API centralisé
 * ============================================================ */

/**
 * Effectue un appel à l'API.
 *
 * Options supplémentaires :
 *
 * requireAuthentication:
 *   true par défaut.
 *
 * redirectOnUnauthorized:
 *   ouvre ou affiche la connexion sur une réponse 401.
 */
async function apiFetch(path, options = {}) {
    const {
        requireAuthentication = true,
        redirectOnUnauthorized = true,
        headers: suppliedHeaders,
        ...fetchOptions
    } = options;

    let normalizedPath = String(path || "");

    if (!normalizedPath.startsWith("/")) {
        normalizedPath = `/${normalizedPath}`;
    }

    const token = getAuthToken();

    if (
        requireAuthentication &&
        !token
    ) {
        if (redirectOnUnauthorized) {
            showLoginModule({
                reason: "required",
                message: "Veuillez vous connecter pour continuer."
            });
        }

        const error = new Error(
            "Authentification requise."
        );

        error.code = "AUTH_REQUIRED";

        throw error;
    }

    const headers = authHeaders(
        suppliedHeaders || {}
    );

    let response;

    try {
        response = await fetch(
            `${API_BASE}${normalizedPath}`,
            {
                ...fetchOptions,
                headers,
                credentials: "same-origin"
            }
        );
    } catch (error) {
        console.error(
            "Erreur réseau pendant l'appel API :",
            error
        );

        const networkError = new Error(
            "API inaccessible. Vérifiez que le service backend est disponible."
        );

        networkError.code = "NETWORK_ERROR";

        throw networkError;
    }

    if (
        response.status === 401 &&
        normalizedPath !== "/api/auth/login"
    ) {
        clearAuthSession();

        dispatchAuthEvent(
            "qrocr:session-expired",
            {
                path: normalizedPath
            }
        );

        if (redirectOnUnauthorized) {
            showLoginModule({
                reason: "expired",
                message:
                    "Votre session a expiré. Veuillez vous reconnecter."
            });
        }
    }

    return response;
}


/* ============================================================
 * Connexion
 * ============================================================ */

async function login(username, password) {
    const normalizedUsername =
        String(username || "").trim();

    if (!normalizedUsername) {
        throw new Error(
            "Le nom d'utilisateur est requis."
        );
    }

    if (
        typeof password !== "string" ||
        password === ""
    ) {
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

                credentials: "same-origin",

                body: JSON.stringify({
                    username: normalizedUsername,
                    password
                })
            }
        );
    } catch (error) {
        console.error(
            "Impossible de joindre l'API de connexion :",
            error
        );

        throw new Error(
            "Connexion impossible : le service backend est inaccessible."
        );
    }

    if (!response.ok) {
        const message = await readApiError(
            response,
            "Identifiants incorrects ou compte inactif."
        );

        throw new Error(message);
    }

    const data = await response.json();

    if (!data.access_token) {
        throw new Error(
            "La réponse de connexion ne contient aucun jeton d'accès."
        );
    }

    saveAuthSession(
        data.access_token,
        data.user || null
    );

    loginRequiredInProgress = false;

    dispatchAuthEvent(
        "qrocr:login-success",
        {
            user: data.user || null
        }
    );

    return data;
}


/**
 * Connecte l'utilisateur puis revient automatiquement à la page demandée.
 */
async function loginAndReturn(
    username,
    password,
    fallbackUrl = DEFAULT_PAGE_AFTER_LOGIN
) {
    const data = await login(
        username,
        password
    );

    const returnUrl =
        consumeReturnUrl() ||
        fallbackUrl;

    if (
        returnUrl &&
        isSafeInternalUrl(returnUrl)
    ) {
        window.location.replace(returnUrl);
    }

    return data;
}


/* ============================================================
 * Validation de la session
 * ============================================================ */

async function fetchCurrentUser() {
    const token = getAuthToken();

    if (!token) {
        return null;
    }

    const response = await apiFetch(
        "/api/auth/me",
        {
            method: "GET",
            cache: "no-store",
            redirectOnUnauthorized: false
        }
    );

    if (response.status === 401) {
        clearAuthSession();

        return null;
    }

    if (!response.ok) {
        const message = await readApiError(
            response,
            "Impossible de vérifier la session."
        );

        throw new Error(message);
    }

    const user = await response.json();

    localStorage.setItem(
        AUTH_USER_KEY,
        JSON.stringify(user)
    );

    return user;
}


/**
 * Évite de lancer plusieurs appels /api/auth/me simultanément.
 */
function loadCurrentUser({
    force = false
} = {}) {
    if (
        authenticationPromise &&
        !force
    ) {
        return authenticationPromise;
    }

    authenticationPromise = fetchCurrentUser()
        .finally(() => {
            authenticationPromise = null;
        });

    return authenticationPromise;
}


/* ============================================================
 * Vérification des rôles
 * ============================================================ */

function hasRole(user, roles) {
    if (
        !user ||
        !Array.isArray(roles) ||
        roles.length === 0
    ) {
        return true;
    }

    return roles.includes(user.role);
}


function isAdmin(
    user = getStoredUser()
) {
    return Boolean(
        user &&
        (
            user.role === "admin" ||
            user.role === "superadmin"
        )
    );
}


function isSuperAdmin(
    user = getStoredUser()
) {
    return Boolean(
        user &&
        user.role === "superadmin"
    );
}


/* ============================================================
 * Protection d'une page
 * ============================================================ */

/**
 * À appeler sur chaque page protégée.
 *
 * Exemples :
 *
 * await qrOcrAuth.requireAuth();
 *
 * await qrOcrAuth.requireAuth({
 *     roles: ["admin", "superadmin"]
 * });
 */
async function requireAuth({
    roles = null,
    rememberPage = true
} = {}) {
    if (rememberPage && !isLoginPage()) {
        rememberReturnUrl();
    }

    if (!getAuthToken()) {
        showLoginModule({
            reason: "required",
            rememberCurrentPage: rememberPage,
            message: "Veuillez vous connecter pour accéder à cette page."
        });

        return null;
    }

    let user;

    try {
        user = await loadCurrentUser();
    } catch (error) {
        console.error(
            "La session n'a pas pu être vérifiée :",
            error
        );

        /*
         * Une erreur réseau ne signifie pas nécessairement que le jeton
         * est invalide. On affiche donc une erreur sans supprimer la session.
         */
        dispatchAuthEvent(
            "qrocr:auth-error",
            {
                message: error.message
            }
        );

        throw error;
    }

    if (!user) {
        showLoginModule({
            reason: "expired",
            rememberCurrentPage: rememberPage,
            message:
                "Votre session a expiré. Veuillez vous reconnecter."
        });

        return null;
    }

    if (
        Array.isArray(roles) &&
        roles.length > 0 &&
        !hasRole(user, roles)
    ) {
        dispatchAuthEvent(
            "qrocr:access-denied",
            {
                user,
                requiredRoles: roles
            }
        );

        throw new Error(
            "Vous ne disposez pas des droits nécessaires pour accéder à cette page."
        );
    }

    localStorage.removeItem(
        AUTH_RETURN_URL_KEY
    );

    dispatchAuthEvent(
        "qrocr:auth-ready",
        {
            user
        }
    );

    return user;
}


/* ============================================================
 * Déconnexion
 * ============================================================ */

function logout({
    redirect = true,
    reason = "logout"
} = {}) {
    clearAuthSession();

    dispatchAuthEvent(
        "qrocr:logout",
        {
            reason
        }
    );

    if (redirect) {
        window.location.replace(
            `${LOGIN_PAGE}?login=${encodeURIComponent(reason)}`
        );
    }
}


/* ============================================================
 * Santé de l'API
 * ============================================================ */

async function checkApiHealth() {
    try {
        const response = await fetch(
            `${API_BASE}/api/health`,
            {
                method: "GET",
                cache: "no-store",
                credentials: "same-origin"
            }
        );

        if (!response.ok) {
            return false;
        }

        const data = await response.json();

        return data.status === "ok";
    } catch {
        return false;
    }
}


/* ============================================================
 * Synchronisation entre onglets
 * ============================================================ */

function handleRemoteAuthEvent(event) {
    if (
        !event ||
        typeof event.type !== "string"
    ) {
        return;
    }

    switch (event.type) {
        case "login":
            authenticationPromise = null;

            dispatchAuthEvent(
                "qrocr:remote-login",
                event.data || {}
            );

            /*
             * Une page de connexion ouverte dans un autre onglet peut
             * revenir automatiquement à la page demandée.
             */
            if (isLoginPage()) {
                const returnUrl =
                    consumeReturnUrl();

                if (returnUrl) {
                    window.location.replace(
                        returnUrl
                    );
                }
            }

            break;

        case "logout":
            clearAuthSession({
                broadcast: false
            });

            dispatchAuthEvent(
                "qrocr:remote-logout"
            );

            if (!isLoginPage()) {
                rememberReturnUrl();

                showLoginModule({
                    reason: "logout",
                    rememberCurrentPage: false,
                    message:
                        "La session a été fermée dans un autre onglet."
                });
            }

            break;

        default:
            break;
    }
}


if (authChannel) {
    authChannel.addEventListener(
        "message",
        (messageEvent) => {
            handleRemoteAuthEvent(
                messageEvent.data
            );
        }
    );
}


window.addEventListener(
    "storage",
    (storageEvent) => {
        if (
            storageEvent.key === AUTH_EVENT_KEY &&
            storageEvent.newValue
        ) {
            try {
                handleRemoteAuthEvent(
                    JSON.parse(
                        storageEvent.newValue
                    )
                );
            } catch (error) {
                console.warn(
                    "Événement d'authentification inter-onglets invalide.",
                    error
                );
            }
        }

        /*
         * Si le jeton est supprimé directement dans un autre onglet.
         */
        if (
            storageEvent.key === AUTH_TOKEN_KEY &&
            storageEvent.newValue === null &&
            !isLoginPage()
        ) {
            showLoginModule({
                reason: "logout",
                message:
                    "La session a été fermée dans un autre onglet."
            });
        }
    }
);


/* ============================================================
 * Initialisation automatique facultative
 * ============================================================ */

/**
 * Protège automatiquement une page si l'élément body contient :
 *
 *   data-auth-required="true"
 *
 * Pour limiter les rôles :
 *
 *   data-auth-roles="admin,superadmin"
 */
async function initializePageAuthentication() {
    const body = document.body;

    if (!body) {
        return null;
    }

    const authRequired =
        body.dataset.authRequired === "true";

    if (!authRequired) {
        return null;
    }

    const roles = body.dataset.authRoles
        ? body.dataset.authRoles
            .split(",")
            .map((role) => role.trim())
            .filter(Boolean)
        : null;

    try {
        return await requireAuth({
            roles
        });
    } catch (error) {
        console.error(
            "Initialisation de l'authentification impossible :",
            error
        );

        return null;
    }
}


document.addEventListener(
    "DOMContentLoaded",
    () => {
        initializePageAuthentication();
    }
);


/* ============================================================
 * API globale
 * ============================================================ */

window.qrOcrAuth = {
    API_BASE,

    getToken: getAuthToken,
    getUser: getStoredUser,

    login,
    loginAndReturn,
    logout,

    loadCurrentUser,
    requireAuth,
    initializePageAuthentication,

    apiFetch,
    authHeaders,
    jsonAuthHeaders,
    readApiError,

    isAdmin,
    isSuperAdmin,
    hasRole,

    checkApiHealth,

    showLoginModule,
    rememberReturnUrl,
    consumeReturnUrl,

    clearSession: clearAuthSession
};


/* ============================================================
 * Compatibilité : API window.AppAuth utilisée par les pages
 * et par app-navigation.js
 * ============================================================ */

window.AppAuth = {
    API_BASE,

    token: getAuthToken,
    user: getStoredUser,
    headers: authHeaders,
    apiFetch,
    requireAuth,
    isAdmin,
    isSuperAdmin,
    hasRole,

    async login(username, password) {
        dispatchAuthEvent("app-auth-login-start");

        try {
            const data = await login(username, password);

            dispatchAuthEvent(
                "app-auth-login",
                { user: data.user || null }
            );

            return data.user || null;
        } catch (error) {
            dispatchAuthEvent(
                "app-auth-login-failure",
                { message: error.message }
            );

            throw error;
        }
    },

    logout({ redirect = null } = {}) {
        logout({ redirect: false });

        if (redirect) {
            window.location.replace(redirect);
        }
    }
};

window.addEventListener(
    "qrocr:logout",
    () => dispatchAuthEvent("app-auth-logout")
);

})();