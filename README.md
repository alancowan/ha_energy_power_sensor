# Energy & Power Sensor

A Home Assistant custom integration (installable through HACS) that creates an **energy** or **power** sensor by adding and subtracting other sensors, with unit conversion and outlier protection.

## Features

- **Two sensor types**: energy (`kWh`, `Wh`, `MJ`, …) or power (`W`, `kW`, …).
- **Sum list and subtract list**: add any number of sensors to each.
- **Per-input units**: each input can use the unit it reports (`Auto`) or a unit you choose, e.g. for template sensors without a unit. All values are converted to the output unit you choose.
- **Update mode**: recalculate whenever any input changes (default), or every *N* seconds (1–3600).
- **Energy outlier protection** (optional *maximum power for the group*, in W).
- **Power outlier threshold** (optional): above this value the sensor keeps its previous value.
- **Ignore unavailable inputs** (power, optional): count unavailable inputs as 0 instead of making the sensor unavailable.
- **Repair issue for missing inputs**: you are told under **Settings → Repairs** when an input entity no longer exists.

## Installation

### HACS

1. In HACS, open the menu → **Custom repositories**.
2. Add this repository's URL with category **Integration**.
3. Install **Energy & Power Sensor** and restart Home Assistant.

### Manual

Copy `custom_components/energy_power_sensor` into your Home Assistant `config/custom_components/` folder and restart.

## Setup

**Settings → Devices & services → Helpers → Create helper → Energy & Power Sensor**

1. Enter a name and pick **Energy** or **Power**.
2. Choose the sensors to add, the sensors to subtract, the output unit, the update interval (`0` = on every change), and the outlier limit.
3. Set the unit of each input. `Auto` uses the unit the sensor reports. A sensor that has no usable unit must have its unit set explicitly.

Everything except the name and type can be changed later via **Configure** on the helper.

## How it works

### Energy sensors

The output is `Σ sum inputs − Σ subtract inputs`, with a stored offset per input.

When a maximum power is set, every time an input changes the integration calculates the **average power** it implies:

```
power = |change in energy| / time since that input last changed
```

If that exceeds the maximum power, the change is treated as an outlier: the whole jump is added to the input's stored offset, so the total stays where it was for that cycle and normal counting resumes on the next one. This handles:

- **A meter being replaced** and starting again from 0 (large decrease).
- **A faulty reading** adding a huge value (large increase).

Because the check is based on power rather than the raw change, a big change after Home Assistant has been offline is accepted as long as the average power over the offline period is plausible. For example, with a 5 kW limit, 30 kWh after 10 hours offline (3 kW average) is counted normally.

Changes are judged over at least 60 seconds, so two readings that arrive a second apart are not mistaken for an extreme power.

Other details:

- Offsets and the last reading of each input are saved and restored across restarts.
- If an input is unavailable, its last known value is used so the total doesn't drop. When it comes back, the change is checked as above.
- Inputs present when the sensor is created count with their full value. Inputs added later start from zero so they don't cause a jump. Removed inputs keep what they contributed.
- The sensor uses state class `total`, so it can be used in the Energy dashboard, including when subtraction makes it go down.
- Attributes `outliers_detected`, `last_outlier_entity` and `last_outlier_time` show what was corrected. Each correction is also logged as a warning.

### Power sensors

The output is `Σ sum inputs − Σ subtract inputs`, converted to the output unit.

- If an outlier threshold is set and the result's absolute value is above it, the sensor keeps its previous value. The count is shown in the `outliers_ignored` attribute.
- By default, if any input is unavailable, the sensor becomes unavailable rather than showing a misleading total. With **Ignore unavailable inputs** on, unavailable inputs count as 0 and the sensor only becomes unavailable when all inputs are.

### Missing and unavailable inputs

- The `unavailable_entities` attribute lists inputs that currently have no usable value (unavailable, unknown, non-numeric, or with an unusable unit).
- If an input entity no longer exists in Home Assistant (deleted or renamed), a repair issue appears under **Settings → Repairs** and a warning is logged. This is only checked once Home Assistant has finished starting, so integrations that are still loading don't trigger it. The issue clears when the entity exists again or you remove it from the list via **Configure**.

## Development

```bash
pip install -r requirements_test.txt
pytest
```
