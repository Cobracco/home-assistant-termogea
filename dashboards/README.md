# Dashboard Termogea UI Style

Questa dashboard replica lo stile dell'app Termogea:

- griglia zone arancione con temperatura grande
- toggle rapido On/Off su hold
- vista dettaglio con card `thermostat`

## File

- `dashboards/termogea_ui_style.yaml`

## Prerequisiti

La dashboard usa card HACS:

- `custom:button-card`
- `custom:auto-entities`

## Installazione

1. Installa le due card da HACS (Frontend).
2. In Home Assistant vai su **Impostazioni > Dashboard > Aggiungi dashboard**.
3. Importa il file YAML `dashboards/termogea_ui_style.yaml`.
4. Se hai altri `climate` non Termogea, aggiungi filtri `exclude` nella card `auto-entities`.
