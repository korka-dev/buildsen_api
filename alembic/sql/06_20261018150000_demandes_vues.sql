-- Dernière consultation des demandes par le professionnel (ajout uniquement)
-- AlterTable
ALTER TABLE "ProProfile" ADD COLUMN     "requestsSeenAt" TIMESTAMP(3);
