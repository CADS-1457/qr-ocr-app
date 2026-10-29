BEGIN;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.identity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id uuid NOT NULL
        REFERENCES core.app_user(id)
        ON DELETE CASCADE,

    provider varchar(50) NOT NULL DEFAULT 'local',
    provider_subject text,
    enabled boolean NOT NULL DEFAULT true,
    mfa_required boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT auth_identity_user_provider_unique
        UNIQUE (user_id, provider),

    CONSTRAINT auth_identity_provider_subject_unique
        UNIQUE (provider, provider_subject)
);

CREATE INDEX IF NOT EXISTS auth_identity_user_idx
    ON auth.identity(user_id);

CREATE INDEX IF NOT EXISTS auth_identity_provider_subject_idx
    ON auth.identity(provider, provider_subject);


CREATE TABLE IF NOT EXISTS auth.authenticator (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    identity_id uuid NOT NULL
        REFERENCES auth.identity(id)
        ON DELETE CASCADE,

    type varchar(30) NOT NULL,
    name text,
    enabled boolean NOT NULL DEFAULT true,

    credential_id bytea,
    public_key bytea,
    sign_count bigint,

    secret_encrypted bytea,

    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz,

    CONSTRAINT auth_authenticator_type_check
        CHECK (type IN ('webauthn', 'totp'))
);

CREATE UNIQUE INDEX IF NOT EXISTS
    auth_authenticator_credential_id_unique
    ON auth.authenticator(credential_id)
    WHERE credential_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS auth_authenticator_identity_idx
    ON auth.authenticator(identity_id);


CREATE TABLE IF NOT EXISTS auth.session (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    identity_id uuid NOT NULL
        REFERENCES auth.identity(id)
        ON DELETE CASCADE,

    token_hash bytea NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,

    mfa_verified_at timestamptz,
    step_up_verified_at timestamptz,
    revoked_at timestamptz,

    ip_address inet,
    user_agent text,

    CONSTRAINT auth_session_token_hash_unique
        UNIQUE (token_hash),

    CONSTRAINT auth_session_expiry_check
        CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS auth_session_identity_idx
    ON auth.session(identity_id);

CREATE INDEX IF NOT EXISTS auth_session_expires_idx
    ON auth.session(expires_at);


CREATE TABLE IF NOT EXISTS auth.recovery_code (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    identity_id uuid NOT NULL
        REFERENCES auth.identity(id)
        ON DELETE CASCADE,

    code_hash bytea NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    used_at timestamptz,
    revoked_at timestamptz,

    CONSTRAINT auth_recovery_code_hash_unique
        UNIQUE (code_hash)
);

CREATE INDEX IF NOT EXISTS auth_recovery_code_identity_idx
    ON auth.recovery_code(identity_id);


CREATE TABLE IF NOT EXISTS auth.authentication_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    identity_id uuid
        REFERENCES auth.identity(id)
        ON DELETE SET NULL,

    user_id uuid
        REFERENCES core.app_user(id)
        ON DELETE SET NULL,

    session_id uuid
        REFERENCES auth.session(id)
        ON DELETE SET NULL,

    event_type varchar(60) NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    success boolean NOT NULL,

    ip_address inet,
    user_agent text,
    request_id text,

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS auth_event_identity_idx
    ON auth.authentication_event(identity_id);

CREATE INDEX IF NOT EXISTS auth_event_user_idx
    ON auth.authentication_event(user_id);

CREATE INDEX IF NOT EXISTS auth_event_occurred_at_idx
    ON auth.authentication_event(occurred_at);


CREATE TABLE IF NOT EXISTS auth.policy (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    organization_id uuid NOT NULL
        REFERENCES core.organization(id)
        ON DELETE CASCADE,

    mfa_required boolean NOT NULL DEFAULT true,

    session_max_minutes integer NOT NULL DEFAULT 720,
    idle_timeout_minutes integer NOT NULL DEFAULT 20,
    mfa_max_age_minutes integer NOT NULL DEFAULT 720,

    step_up_enabled boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT auth_policy_organization_unique
        UNIQUE (organization_id),

    CONSTRAINT auth_policy_session_max_check
        CHECK (session_max_minutes > 0),

    CONSTRAINT auth_policy_idle_timeout_check
        CHECK (idle_timeout_minutes > 0),

    CONSTRAINT auth_policy_mfa_max_age_check
        CHECK (mfa_max_age_minutes > 0)
);

COMMIT;
