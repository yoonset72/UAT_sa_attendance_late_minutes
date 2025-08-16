from odoo import fields, api, _, models
from datetime import datetime, time as dt_time
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

            if not r.employee_id:
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

            # Use check_in if present
            first_punch = r.check_in
            if not first_punch:
                continue

            # Localize punch time
            punch_dt = fields.Datetime.context_timestamp(r, first_punch)
            weekday_str = str(punch_dt.weekday())  # 0=Monday

            _logger.info("Check-in: %s (weekday %s)", punch_dt, weekday_str)

            # Gather all shift start times for that weekday across all calendars
            possible_shifts = []
            for cal in calendars:
                for attend in cal.attendance_ids.filtered(lambda a: a.dayofweek == weekday_str and a.day_period != 'break'):
                    possible_shifts.append(attend.hour_from)

            if not possible_shifts:
                continue

            # Find the shift start nearest (earlier than or equal) to check-in
            punch_hour_decimal = punch_dt.hour + punch_dt.minute / 60.0
            nearest_shift_hour = min(possible_shifts, key=lambda h: abs(h - punch_hour_decimal))

            # Build shift start datetime (timezone-aware)
            naive_shift_dt = datetime.combine(
                punch_dt.date(), 
                dt_time(hour=int(nearest_shift_hour), minute=int((nearest_shift_hour % 1) * 60))
            )
            shift_start_dt = tz.localize(naive_shift_dt)

            _logger.info("Nearest shift start time: %s", shift_start_dt)

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
