-- Alerte différée des formules gratuites et signalement des demandes (ajouts uniquement)
-- AlterTable
ALTER TABLE "ServiceRequest" ADD COLUMN     "freeNotifiedAt" TIMESTAMP(3);

-- AlterTable
ALTER TABLE "Report" ADD COLUMN     "requestId" TEXT;

-- AddForeignKey
ALTER TABLE "Report" ADD CONSTRAINT "Report_requestId_fkey" FOREIGN KEY ("requestId") REFERENCES "ServiceRequest"("id") ON DELETE SET NULL ON UPDATE CASCADE;
