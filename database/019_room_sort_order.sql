-- Ordre d'affichage des colonnes de salles dans le calendrier
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS sort_order integer NOT NULL DEFAULT 0;

UPDATE rooms r
SET sort_order = o.rn
FROM (SELECT id, row_number() OVER (ORDER BY name) AS rn FROM rooms) o
WHERE r.id = o.id AND r.sort_order = 0;
