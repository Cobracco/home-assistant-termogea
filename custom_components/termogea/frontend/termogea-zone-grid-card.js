class TermogeaZoneGridCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    if (config && typeof config !== "object") {
      throw new Error("Invalid configuration");
    }
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    const entities = this._getEntities();
    return Math.max(2, Math.ceil(entities.length / 2));
  }

  getGridOptions() {
    return {
      columns: 6,
      rows: 4,
      min_rows: 3,
      min_columns: 3,
    };
  }

  _getEntities() {
    if (!this._hass || !this._hass.states || typeof this._hass.states !== "object") {
      return [];
    }

    const configured = this._config.entities;
    if (Array.isArray(configured) && configured.length > 0) {
      return configured
        .map((entry) => {
          if (typeof entry === "string") {
            return { entity: entry };
          }
          return entry;
        })
        .filter((entry) => typeof entry?.entity === "string");
    }

    return Object.keys(this._hass.states)
      .filter((entityId) => entityId.startsWith("climate.termogea_"))
      .sort()
      .map((entityId) => ({ entity: entityId }));
  }

  _nameFor(entry, stateObj) {
    if (entry.name) {
      return entry.name;
    }
    if (stateObj?.attributes?.friendly_name) {
      return stateObj.attributes.friendly_name;
    }
    return entry.entity;
  }

  _isOn(stateObj) {
    const mode = stateObj?.state;
    return mode && mode !== "off" && mode !== "unavailable" && mode !== "unknown";
  }

  _formatTemp(value) {
    if (value === undefined || value === null || Number.isNaN(Number(value))) {
      return "--";
    }
    return Number(value).toFixed(1);
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }

    try {
      if (!this._hass || !this._hass.states || typeof this._hass.states !== "object") {
        this.shadowRoot.innerHTML = "<ha-card><div class='empty'>Anteprima scheda Termogea.</div></ha-card>";
        return;
      }

      const title = this._config.title || "Termogea";
      const entities = this._getEntities();
      const cards = entities
        .map((entry) => {
          const stateObj = this._hass.states[entry.entity];
          const name = this._nameFor(entry, stateObj);
          const current = stateObj?.attributes?.current_temperature;
          const humidity = stateObj?.attributes?.current_humidity;
          const target = stateObj?.attributes?.temperature;
          const isOn = this._isOn(stateObj);
          const unavailable = !stateObj || stateObj.state === "unavailable";

          return `
            <div class="zone ${isOn ? "on" : "off"} ${unavailable ? "unavailable" : ""}" data-action="more_info" data-entity="${entry.entity}" tabindex="0" role="button">
              <div class="zone-name">${name}</div>
              <div class="zone-temp">${this._formatTemp(current)}<span class="unit">°C</span></div>
              <div class="zone-target">Target ${this._formatTemp(target)}°C · UR ${this._formatTemp(humidity)}%</div>
              <div class="zone-actions">
                <button class="action small" data-action="temp_down" data-entity="${entry.entity}" ${unavailable ? "disabled" : ""}>-</button>
                <button class="action small" data-action="temp_up" data-entity="${entry.entity}" ${unavailable ? "disabled" : ""}>+</button>
                <button class="action toggle ${isOn ? "active" : ""}" data-action="toggle" data-entity="${entry.entity}" ${unavailable ? "disabled" : ""}>
                  ${isOn ? "ON" : "OFF"}
                </button>
              </div>
            </div>
          `;
        })
        .join("");

      this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
        }
        .title {
          font-size: 20px;
          font-weight: 600;
          margin-bottom: 14px;
        }
        .grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        }
        .zone {
          border: none;
          border-radius: 14px;
          color: white;
          cursor: pointer;
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-height: 150px;
          padding: 14px;
          text-align: left;
          width: 100%;
          background: linear-gradient(165deg, #f4a000 0%, #f15a24 85%);
          transition: transform 120ms ease, filter 120ms ease;
        }
        .zone.off {
          filter: saturate(0.65) brightness(0.88);
        }
        .zone.unavailable {
          filter: grayscale(1);
          opacity: 0.7;
        }
        .zone:hover {
          transform: translateY(-1px);
        }
        .zone-name {
          font-size: 20px;
          font-weight: 500;
          line-height: 1.1;
          text-transform: uppercase;
        }
        .zone-temp {
          font-size: 52px;
          font-weight: 700;
          line-height: 1;
        }
        .zone-temp .unit {
          font-size: 22px;
          font-weight: 600;
          margin-left: 2px;
        }
        .zone-target {
          font-size: 15px;
          opacity: 0.95;
        }
        .zone-actions {
          align-items: center;
          display: flex;
          gap: 8px;
          margin-top: auto;
        }
        .action {
          border: 0;
          border-radius: 999px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 700;
          padding: 6px 11px;
        }
        .action.small {
          background: rgba(255, 255, 255, 0.95);
          color: #333;
          width: 34px;
        }
        .action.toggle {
          background: rgba(255, 255, 255, 0.95);
          color: #666;
          margin-left: auto;
          min-width: 58px;
        }
        .action.toggle.active {
          color: #d9412e;
        }
        .action:disabled {
          cursor: default;
          opacity: 0.6;
        }
        .empty {
          color: var(--secondary-text-color);
          padding: 10px 0 6px;
        }
      </style>
      <ha-card>
        <div class="title">${title}</div>
        <div class="grid">
          ${cards || "<div class='empty'>Nessuna zona Termogea trovata.</div>"}
        </div>
      </ha-card>
    `;
    } catch (err) {
      console.error("Termogea zone grid card render error", err);
      this.shadowRoot.innerHTML =
        "<ha-card><div class='empty'>Errore caricamento scheda Termogea. Controlla la console browser.</div></ha-card>";
    }
  }

  _fireEvent(type, detail = {}) {
    this.dispatchEvent(
      new CustomEvent(type, {
        bubbles: true,
        composed: true,
        detail,
      })
    );
  }

  _onClick(event) {
    const actionElement = event.target.closest("[data-action]");
    if (!actionElement || !this._hass) {
      return;
    }
    event.stopPropagation();

    const entityId = actionElement.getAttribute("data-entity");
    const action = actionElement.getAttribute("data-action");
    if (!entityId || !action) {
      return;
    }

    if (action === "more_info") {
      this._fireEvent("hass-more-info", { entityId });
      return;
    }

    const stateObj = this._hass.states[entityId];
    if (!stateObj || stateObj.state === "unavailable") {
      return;
    }

    if (action === "toggle") {
      const mode = this._isOn(stateObj) ? "off" : "heat";
      this._hass.callService("climate", "set_hvac_mode", {
        entity_id: entityId,
        hvac_mode: mode,
      });
      return;
    }

    if (action === "temp_up" || action === "temp_down") {
      const current = Number(stateObj.attributes.temperature ?? stateObj.attributes.current_temperature);
      const min = Number(stateObj.attributes.min_temp ?? 5);
      const max = Number(stateObj.attributes.max_temp ?? 35);
      const step = Number(
        this._config.temperature_step
          ?? stateObj.attributes.target_temp_step
          ?? stateObj.attributes.temperature_step
          ?? 0.5
      );
      if (Number.isNaN(current) || Number.isNaN(step)) {
        return;
      }
      const direction = action === "temp_up" ? 1 : -1;
      const next = Math.min(max, Math.max(min, current + direction * step));
      this._hass.callService("climate", "set_temperature", {
        entity_id: entityId,
        temperature: Number(next.toFixed(1)),
      });
    }
  }
}

if (!customElements.get("termogea-zone-grid-card")) {
  customElements.define("termogea-zone-grid-card", TermogeaZoneGridCard);
}

window.customCards = window.customCards || [];
const TERMOGEA_CARD_TYPE = "termogea-zone-grid-card";
if (!window.customCards.some((card) => card && (card.type === TERMOGEA_CARD_TYPE || card.type === `custom:${TERMOGEA_CARD_TYPE}`))) {
  window.customCards.push({
    type: TERMOGEA_CARD_TYPE,
    name: "Termogea Zone Grid",
    description: "Griglia rapida delle zone Termogea con toggle, setpoint e umidita.",
    preview: false,
    documentationURL: "https://github.com/Cobracco/home-assistant-termogea",
  });
}
