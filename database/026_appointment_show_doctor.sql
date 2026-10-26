-- Choix d'affichage du médecin propre au rendez-vous (prime sur le réglage de la salle)
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS show_doctor BOOLEAN;
