-- Exemples de contrôles après migration
SELECT entity_type, count(*) FROM migration.id_map GROUP BY entity_type ORDER BY entity_type;
SELECT entity_type, count(*) FROM migration.reject GROUP BY entity_type ORDER BY entity_type;
SELECT normalized_value_hmac, count(DISTINCT patient_id) AS patients
FROM patient.contact_point cp JOIN patient.patient_contact pc ON pc.contact_point_id=cp.id
WHERE cp.kind='mobile' GROUP BY normalized_value_hmac HAVING count(DISTINCT patient_id)>1;
SELECT count(*) AS orphan_appointments FROM agenda.appointment a LEFT JOIN patient.patient p ON p.id=a.patient_id WHERE a.patient_id IS NOT NULL AND p.id IS NULL;
