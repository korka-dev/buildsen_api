-- Statut « Entreprise » et nom de l'entreprise
ALTER TYPE "ProStatut" ADD VALUE 'ENT';
ALTER TABLE "ProProfile" ADD COLUMN "companyName" TEXT;
