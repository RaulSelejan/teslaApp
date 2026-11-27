"""Read-only dashboard fields from the Fleet API vehicle_data response."""
import math
import re


# Always show these instruments, including when Tesla has no reading for them.
CORE = {
    'charge_state': {
        'battery_level': ('Battery', '%'),
        'est_battery_range': ('Estimated range', 'mi'),
        'charging_state': ('Charging status', ''),
        'charger_voltage': ('Charging voltage', 'V'),
        'charger_actual_current': ('Charging current', 'A'),
        'charger_power': ('Charging power', 'kW'),
    },
    'climate_state': {
        'inside_temp': ('Interior temperature', '°C'),
        'outside_temp': ('Exterior temperature', '°C'),
    },
    'vehicle_state': {
        'odometer': ('Odometer', 'mi'),
        'car_version': ('Software version', ''),
        'tpms_pressure_fl': ('Tyre pressure front left', 'bar'),
        'tpms_pressure_fr': ('Tyre pressure front right', 'bar'),
        'tpms_pressure_rl': ('Tyre pressure rear left', 'bar'),
        'tpms_pressure_rr': ('Tyre pressure rear right', 'bar'),
    },
    'vehicle_config': {'car_type': ('Model', '')},
}

# Only forward known operational fields. The source response can also contain
# navigation, personal identifiers, media and other private account data.
EXTRA = {
    'charge_state': {
        'battery_range': ('Rated range', 'mi'),
        'ideal_battery_range': ('Ideal range', 'mi'),
        'usable_battery_level': ('Usable battery', '%'),
        'charge_limit_soc': ('Charge limit', '%'),
        'charge_limit_soc_min': ('Minimum charge limit', '%'),
        'charge_limit_soc_max': ('Maximum charge limit', '%'),
        'charge_energy_added': ('Energy added', 'kWh'),
        'charge_miles_added_rated': ('Rated range added', 'mi'),
        'charge_rate': ('Range added per hour', 'mph'),
        'minutes_to_full_charge': ('Time to charge target', 'min'),
        'time_to_full_charge': ('Time to charge target (hours)', 'h'),
        'charge_current_request': ('Requested current', 'A'),
        'charge_current_request_max': ('Maximum requested current', 'A'),
        'charger_pilot_current': ('Pilot current', 'A'),
        'charger_phases': ('Charger phases', ''),
        'fast_charger_present': ('Fast charger present', ''),
        'fast_charger_type': ('Fast charger type', ''),
        'fast_charger_brand': ('Fast charger brand', ''),
        'conn_charge_cable': ('Charge cable', ''),
        'charge_port_door_open': ('Charge port open', ''),
        'charge_port_latch': ('Charge port latch', ''),
        'charge_port_cold_weather_mode': ('Cold-weather port mode', ''),
        'battery_heater_on': ('Battery heater', ''),
        'charge_enable_request': ('Charging enabled', ''),
        'scheduled_charging_pending': ('Scheduled charge pending', ''),
        'trip_charging': ('Trip charging', ''),
    },
    'climate_state': {
        'driver_temp_setting': ('Driver setpoint', '°C'),
        'passenger_temp_setting': ('Passenger setpoint', '°C'),
        'min_avail_temp': ('Minimum setpoint', '°C'),
        'max_avail_temp': ('Maximum setpoint', '°C'),
        'is_climate_on': ('Climate on', ''),
        'is_auto_conditioning_on': ('Auto climate', ''),
        'is_preconditioning': ('Preconditioning', ''),
        'fan_status': ('Fan level', ''),
        'hvac_auto_request': ('HVAC mode', ''),
        'climate_keeper_mode': ('Climate keeper', ''),
        'battery_heater': ('Battery heating', ''),
        'is_front_defroster_on': ('Front defroster', ''),
        'is_rear_defroster_on': ('Rear defroster', ''),
        'steering_wheel_heater': ('Steering wheel heater', ''),
        'wiper_blade_heater': ('Wiper blade heater', ''),
        'side_mirror_heaters': ('Mirror heaters', ''),
        'seat_heater_left': ('Driver seat heat', ''),
        'seat_heater_right': ('Passenger seat heat', ''),
        'seat_heater_rear_left': ('Rear left seat heat', ''),
        'seat_heater_rear_right': ('Rear right seat heat', ''),
        'cabin_overheat_protection': ('Cabin overheat protection', ''),
    },
    'vehicle_state': {
        'locked': ('Locked', ''),
        'sentry_mode': ('Sentry mode', ''),
        'valet_mode': ('Valet mode', ''),
        'is_user_present': ('Occupant detected', ''),
        'center_display_state': ('Center display state', ''),
        'df': ('Driver door state', ''),
        'dr': ('Driver rear door state', ''),
        'pf': ('Passenger door state', ''),
        'pr': ('Passenger rear door state', ''),
        'ft': ('Front trunk state', ''),
        'rt': ('Rear trunk state', ''),
        'fd_window': ('Driver window state', ''),
        'fp_window': ('Passenger window state', ''),
        'rd_window': ('Rear driver window state', ''),
        'rp_window': ('Rear passenger window state', ''),
        'tpms_rcp_front_value': ('Recommended cold front pressure', 'bar'),
        'tpms_rcp_rear_value': ('Recommended cold rear pressure', 'bar'),
        'software_update.status': ('Software update status', ''),
        'software_update.version': ('Update version', ''),
        'software_update.download_perc': ('Update downloaded', '%'),
        'software_update.install_perc': ('Update installed', '%'),
        'software_update.expected_duration_sec': ('Expected update duration', 's'),
        **{f'tpms_last_seen_pressure_time_{wheel}': (f'{name} pressure measured', 'unix s')
           for wheel, name in [('fl', 'Front left'), ('fr', 'Front right'),
                               ('rl', 'Rear left'), ('rr', 'Rear right')]},
    },
    'vehicle_config': {
        'trim_badging': ('Trim badge', ''),
        'exterior_color': ('Exterior paint', ''),
        'wheel_type': ('Wheel type', ''),
        'wheel_diameter': ('Wheel diameter', 'in'),
        'spoiler_type': ('Spoiler', ''),
        'roof_color': ('Roof colour', ''),
        'seat_type': ('Seat type', ''),
        'motorized_charge_port': ('Motorized charge port', ''),
        'has_air_suspension': ('Air suspension', ''),
        'has_ludicrous_mode': ('Ludicrous mode equipped', ''),
        'has_seat_cooling': ('Seat cooling equipped', ''),
        'has_ventilated_seats': ('Ventilated seats', ''),
        'use_range_badging': ('Range badging', ''),
        'eu_vehicle': ('European specification', ''),
        'rhd': ('Right-hand drive', ''),
        'charge_port_type': ('Port type', ''),
        'can_accept_navigation_requests': ('Navigation requests supported', ''),
    },
    'gui_settings': {
        'gui_distance_units': ('Vehicle distance units', ''),
        'gui_temperature_units': ('Vehicle temperature units', ''),
        'gui_charge_rate_units': ('Vehicle charge rate units', ''),
        'gui_range_display': ('Range display setting', ''),
        'gui_24_hour_time': ('24-hour clock enabled', ''),
        'show_range_units': ('Display range units', ''),
    },
}

TREND_KEYS = ('charge_state.battery_level', 'charge_state.charger_power',
              'climate_state.inside_temp', 'vehicle_state.odometer')


def field_value(block, field):
    value = block
    for part in field.split('.'):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    if isinstance(value, str):
        return True, re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b', '[masked]', value[:256]) or None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return True, value
    return True, None


def make_snapshot(data, key, cached, read_at):
    signals, values, timestamps = [], {}, {}
    for group, extras in EXTRA.items():
        block = data.get(group)
        required = CORE.get(group, {})
        for field, (label, unit) in (required | extras).items():
            present, value = field_value(block, field)
            if field in required:
                values[label] = 'Unavailable' if value is None else f'{value} {unit}'.strip()
            elif not present:
                continue
            signals.append(dict(group=group, field=field, label=label, unit=unit, value=value))
        stamp = block.get('timestamp') if isinstance(block, dict) else None
        if type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0:
            timestamps[group] = stamp
    return dict(car=key, cached=cached, read_at=read_at, values=values,
                signals=signals, timestamps=timestamps)


def reading_summary(snapshot):
    readings = {}
    for signal in snapshot['signals']:
        key = f"{signal['group']}.{signal['field']}"
        if key in TREND_KEYS and type(signal['value']) in (int, float):
            readings[key] = signal['value']
    return dict(cached=snapshot['cached'], timestamps=snapshot['timestamps'],
                readings=readings, read_at=snapshot['read_at'])
