# Contributing

## Obiettivo

Mantenere il repository coerente con gli standard Home Assistant e GitHub.

## Come contribuire

1. Apri una issue prima di introdurre cambiamenti grandi o breaking.
2. Lavora in branch dedicati.
3. Mantieni patch piccole e focalizzate.
4. Aggiorna documentazione e changelog contestualmente quando il comportamento cambia.
5. Per custom integration Home Assistant:
   - conserva il dominio stabile
   - non rompere `entity_id` e servizi senza motivo forte
   - mantieni `manifest.json` allineato
   - verifica la compatibilita con `hassfest`

## Checklist minima per PR

- codice coerente con lo stile del repository
- documentazione aggiornata
- nessun segreto nel repository
- workflow GitHub verdi quando applicabile
- nessuna modifica distruttiva non documentata

## Segnalazione vulnerabilita

Per questioni di sicurezza usa le indicazioni in [SECURITY.md](SECURITY.md).
