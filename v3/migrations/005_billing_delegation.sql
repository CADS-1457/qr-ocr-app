BEGIN;
ALTER TABLE billing.service
  ADD COLUMN IF NOT EXISTS performed_by_provider_id uuid REFERENCES provider.healthcare_provider(id),
  ADD COLUMN IF NOT EXISTS encoded_by_user_id uuid REFERENCES core.app_user(id),
  ADD COLUMN IF NOT EXISTS encoded_at timestamptz;

CREATE TABLE IF NOT EXISTS billing.delegation (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 facility_id uuid NOT NULL REFERENCES core.facility(id),
 delegate_user_id uuid NOT NULL REFERENCES core.app_user(id),
 provider_id uuid NOT NULL REFERENCES provider.healthcare_provider(id),
 can_encode boolean NOT NULL DEFAULT true,
 can_assign_provider boolean NOT NULL DEFAULT false,
 can_validate boolean NOT NULL DEFAULT false,
 can_submit boolean NOT NULL DEFAULT false,
 valid_from timestamptz NOT NULL DEFAULT now(),
 valid_to timestamptz,
 authorization_reference text,
 created_by uuid REFERENCES core.app_user(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 revoked_at timestamptz,
 CHECK(valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX IF NOT EXISTS idx_billing_delegation_active
 ON billing.delegation(facility_id, delegate_user_id, provider_id, valid_from, valid_to);

ALTER TABLE billing.claim
  ADD COLUMN IF NOT EXISTS validated_by_user_id uuid REFERENCES core.app_user(id),
  ADD COLUMN IF NOT EXISTS validated_at timestamptz,
  ADD COLUMN IF NOT EXISTS submitted_by_user_id uuid REFERENCES core.app_user(id),
  ADD COLUMN IF NOT EXISTS delegation_id uuid REFERENCES billing.delegation(id);
COMMIT;
