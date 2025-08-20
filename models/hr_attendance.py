from odoo import fields, api, _, models
from datetime import datetime as dt, time as dt_time, timedelta
import pytz
import logging


_logger = logging.getLogger(__name__)

class SaAttendance(models.Model):
    _inherit = "hr.attendance"

    currency_id = fields.Many2one(
        'res.currency', 
        'Currency', 
        store=True, 
        related='employee_id.company_id.currency_id', 
        tracking=True
    )
    waved = fields.Boolean(default=False, tracking=True)
    late_minutes = fields.Integer(
        readonly=False, 
        pre_compute=True,  
        store=True, 
        compute="_compute_late_minutes", 
        tracking=True
    )
    deduction_amount = fields.Monetary(
        store=True, 
        pre_compute=True, 
        compute="_compute_deduction", 
        readonly=False, 
        tracking=True
    )
    display_late_minutes = fields.Float(
        readonly=False, 
        string="Late", 
        pre_compute=True,  
        store=True, 
        compute="_compute_late_minutes"
    )

    @api.depends("employee_id", "check_in", "check_out")
    def _compute_late_minutes(self):
        for r in self:
            r.late_minutes = 0
            r.display_late_minutes = 0

            if not r.employee_id or not r.check_in:
                continue

            # Collect all calendars for employee
            calendars = r.employee_id.resource_calendar_ids
            if not calendars and r.employee_id.resource_calendar_id:
                calendars = r.employee_id.resource_calendar_id
            if not calendars:
                continue

            # Ensure timezone exists
            if not r.employee_id.tz:
                continue
            tz = pytz.timezone(r.employee_id.tz)

            # Localize punch time
            punch_dt = fields.Datetime.context_timestamp(r, r.check_in)

            # Find all shifts that could include this punch
            possible_shifts = []
            for cal in calendars:
                for attend in cal.attendance_ids.filtered(lambda a: a.day_period != 'break'):
                    shift_start = attend.hour_from
                    shift_end = attend.hour_to
                    crosses_midnight = shift_end < shift_start
                    possible_shifts.append({
                        "start": shift_start,
                        "end": shift_end,
                        "crosses_midnight": crosses_midnight,
                        "calendar": cal,
                        "dayofweek": int(attend.dayofweek)
                    })

            if not possible_shifts:
                continue

            nearest_shift = None
            min_diff = 9999

            punch_hour_decimal = punch_dt.hour + punch_dt.minute / 60.0

            for shift in possible_shifts:
                start, end, crosses_midnight = shift["start"], shift["end"], shift["crosses_midnight"]
                
                # Determine punch relation
                if crosses_midnight:
                    # Shift from previous day to today
                    if punch_hour_decimal < end:
                        # punch after midnight → part of previous day shift
                        diff = 0  # consider late from previous day start
                    elif punch_hour_decimal >= start:
                        diff = abs(punch_hour_decimal - start)
                    else:
                        diff = abs(punch_hour_decimal - start)
                else:
                    # Normal shift
                    diff = abs(punch_hour_decimal - start)

                if diff < min_diff:
                    min_diff = diff
                    nearest_shift = shift

            if not nearest_shift:
                continue

            # Compute shift start datetime
            shift_start_hour = int(nearest_shift["start"])
            shift_start_min = int(round((nearest_shift["start"] % 1) * 60))

            if nearest_shift["crosses_midnight"] and punch_hour_decimal < nearest_shift["end"]:
                shift_date = (punch_dt - timedelta(days=1)).date()
            else:
                shift_date = punch_dt.date()

            naive_shift_dt = dt.combine(shift_date, dt_time(shift_start_hour, shift_start_min))
            shift_start_dt = tz.localize(naive_shift_dt)

            # Calculate late minutes
            diff_minutes = (punch_dt - shift_start_dt).total_seconds() / 60
            r.late_minutes = max(0, int(diff_minutes))
            r.display_late_minutes = round(r.late_minutes / 60.0, 2)

    @staticmethod
    def _float_hour_to_time(float_hour):
        """Convert a float hour (e.g., 8.5) to a time object (08:30)."""
        hours = int(float_hour)
        minutes = int(round((float_hour % 1) * 60))
        return dt_time(hour=hours, minute=minutes)


    @api.model_create_multi
    def create(self, vals_list):
        result = super(SaAttendance, self).create(vals_list)
        result._compute_late_minutes()
        result._compute_deduction()
        return result

    @api.depends("employee_id", "late_minutes")
    def _compute_deduction(self):
        for r in self:
            if not r.employee_id.attendance_rule_id or r.late_minutes == 0:
                r.deduction_amount = 0.0
                continue
            r.deduction_amount = r.employee_id.attendance_rule_id._compute_deduction(
                r.employee_id, r.check_in, r.late_minutes
            )

    def get_compute_deduction(self):
        for r in self:
            r._compute_deduction()

    def action_wave_deduction(self):
        for r in self:
            r.waved = True

    def action_unwave_deduction(self):
        for r in self:
            r.waved = False
