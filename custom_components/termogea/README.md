# Termogea for Home Assistant

Custom integration Home Assistant per controllare e configurare impianti **Termogea** con persistenza nativa, policy di presenza e schedulazione settimanale.

Asset grafici repository:

- `icon.png`
- `logo.png`

## Funzionalita

- login e polling locale verso il controller Termogea
- entita `climate` per zona con mapping registri `mod/reg`
- configurazione persistente via UI Home Assistant
- CRUD zone dalla UI dell’integrazione
- associazione persone e sensori di presenza per zona
- preset persistenti per zona:
  - `comfort`
  - `eco`
  - `away`
  - `night`
  - `inactive/off`
- fasce orarie settimanali persistenti
- sensori di policy per debug e dashboard
- import legacy da file YAML

## Installazione manuale

1. Copia `custom_components/termogea` nella directory `custom_components` della tua configurazione Home Assistant.
2. Riavvia Home Assistant.
3. Vai in **Settings > Devices & Services > Add Integration**.
4. Cerca `Termogea`.

## Configurazione

La configurazione primaria avviene dalla UI dell’integrazione:

- connessione controller
- parametri globali
- fasce orarie
- zone
- persone assegnate
- sensori presenza
- preset zona
- mapping tecnico registri

### Import legacy opzionale

Se hai un vecchio file `termogea_zones.yaml`, puoi importarlo:

- dalla UI di configurazione dell’integrazione
- oppure con il servizio `termogea.import_legacy_yaml`

## Limitazioni

- il mapping tecnico dei registri zona deve essere noto
- per installazione HACS dedicata e raccomandato un repository separato con un solo dominio custom integration

## Supporto

- bug report: [GitHub Issues](https://github.com/Cobracco/home-assistant-termogea/issues)
- sicurezza: [SECURITY.md](../../SECURITY.md)
- contributi: [CONTRIBUTING.md](../../CONTRIBUTING.md)
