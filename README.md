# Home Assistant Termogea

Custom integration Home Assistant per controllare e configurare impianti **Termogea** con persistenza nativa, policy per presenza persone, sensori locali e schedulazione settimanale.

![Termogea Logo](logo.png)

## Repository

- GitHub owner: `Cobracco`
- Repository target: `home-assistant-termogea`
- Tipo: custom integration Home Assistant

## Funzionalita

- login locale verso il controller Termogea
- entita `climate` per zona
- configurazione persistente via UI Home Assistant
- CRUD zone dalla UI dell'integrazione
- associazione persone e sensori di presenza per zona
- preset persistenti:
  - comfort
  - eco
  - away
  - night
  - inactive/off
- fasce orarie settimanali persistenti
- sensori di policy per debug e dashboard
- import legacy da YAML

## Installazione manuale

1. Copia `custom_components/termogea` dentro la directory `custom_components` della tua configurazione Home Assistant.
2. Riavvia Home Assistant.
3. Vai in **Settings > Devices & Services > Add Integration**.
4. Cerca `Termogea`.

## Installazione via HACS

Il repository e strutturato come custom integration standalone con un solo dominio.

1. Apri HACS.
2. Vai su **Integrations**.
3. Aggiungi il repository personalizzato `https://github.com/Cobracco/home-assistant-termogea`.
4. Seleziona categoria `Integration`.
5. Installa `Termogea`.

## Configurazione

La configurazione primaria avviene dalla UI dell'integrazione:

- connessione controller
- parametri globali
- fasce orarie
- zone
- persone assegnate
- sensori presenza
- preset zona
- mapping tecnico registri

## Import legacy

Se hai un file `termogea_zones.yaml`, puoi importarlo:

- dalla UI di configurazione dell'integrazione
- oppure con il servizio `termogea.import_legacy_yaml`

## Dashboard UI stile app

Nel repository e inclusa una dashboard Lovelace in stile Termogea:

- file: `dashboards/termogea_ui_style.yaml`
- docs: `dashboards/README.md`

Prerequisiti HACS frontend:

- `button-card`
- `auto-entities`

## Sviluppo

- validation workflow: `hassfest`
- issue templates GitHub inclusi
- `CODEOWNERS`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md` inclusi
- branding assets HACS/GitHub:
  - `icon.png`
  - `logo.png`

## Supporto

- Issues: [GitHub Issues](https://github.com/Cobracco/home-assistant-termogea/issues)
- Security: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
