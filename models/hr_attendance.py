from odoo import fields, api, _, models
from datetime import datetime as dt, time as dt_time, timedelta
import pytz
import logging
import datetime

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

    @api.depends("employee_id", "check_in")
    def _compute_late_minutes(self):
        for rec in self:
            rec.late_minutes = 0
            rec.display_late_minutes = 0

            if not rec.employee_id or not rec.check_in:
                continue

            # Use Asia/Rangoon timezone
            tz = pytz.timezone("Asia/Rangoon")

            # Convert UTC check_in to local time
            punch_dt_utc = fields.Datetime.from_string(rec.check_in)
            punch_dt = (punch_dt_utc.astimezone(tz) if punch_dt_utc.tzinfo
                        else pytz.UTC.localize(punch_dt_utc).astimezone(tz))
            punch_date = punch_dt.date()
            punch_time = punch_dt.time()

            # Get shift lines, ignoring any that start at 00:00
            calendars = rec.employee_id.resource_calendar_ids
            if not calendars:
                continue

            candidate_starts = []
            for cal in calendars:
                lines_today = cal.attendance_ids.filtered(
                    lambda a: int(a.dayofweek) == punch_date.weekday() and a.day_period != 'break'
                )
                for line in lines_today:
                    shift_start = self._float_hour_to_time(line.hour_from)
                    if shift_start != datetime.time(0, 0):  # Ignore 00:00 shifts
                        candidate_starts.append(shift_start)

            candidate_starts = sorted(candidate_starts)
            if not candidate_starts:
                continue

            # Helper: calculate minutes late from a reference time
            def calc_late(ref_time):
                ref_dt = tz.localize(datetime.datetime.combine(punch_date, ref_time))
                return max(0, int((punch_dt - ref_dt).total_seconds() / 60))

            first_shift = candidate_starts[0]
            second_shift = candidate_starts[1] if len(candidate_starts) > 1 else None
            third_shift = candidate_starts[2] if len(candidate_starts) > 2 else None
            fallback_1645 = datetime.time(16, 45)
            cutoff_12 = datetime.time(12, 0)
            cutoff_15 = datetime.time(15, 0)

            late_minutes = 0
            used_shift = first_shift

            # Rule 1: Before first shift -> no late
            if punch_time < first_shift:
                late_minutes = 0
                used_shift = first_shift

            # Rule 2: Between first shift start and 12:00 -> late from first shift
            elif first_shift <= punch_time < cutoff_12:
                late_minutes = calc_late(first_shift)
                used_shift = first_shift

            # Rule 3: Between 12:00 and second shift start -> no late
            elif second_shift and cutoff_12 <= punch_time < second_shift:
                late_minutes = 0
                used_shift = second_shift

            # Rule 4: Between second shift start and 15:00 -> late from second shift
            elif second_shift and second_shift <= punch_time < cutoff_15:
                late_minutes = calc_late(second_shift)
                used_shift = second_shift

            # Rule 5: Between 15:00 and third shift start (if exists) -> no late
            elif third_shift and cutoff_15 <= punch_time < third_shift:
                late_minutes = 0
                used_shift = third_shift

            # Rule 6: After last shift -> late from 16:45 (fallback) if time >= 16:45
            elif punch_time >= fallback_1645:
                late_minutes = calc_late(fallback_1645)
                used_shift = fallback_1645

            # Update record
            rec.late_minutes = late_minutes
            rec.display_late_minutes = round(late_minutes / 60.0, 2)

            # Debug log
            _logger.info(
                "[LATE] Employee: %s | Punch: %s | Used Shift Start: %s | Late Minutes: %d",
                rec.employee_id.name,
                punch_dt.strftime("%m/%d/%Y %H:%M:%S"),
                used_shift.strftime("%H:%M"),
                late_minutes
            )

    @staticmethod
    def _float_hour_to_time(float_hour):
        h = int(float_hour)
        m = int(round((float_hour - h) * 60))
        return datetime.time(h, m)
    
    @staticmethod
    def _time_to_minutes(t):
        return t.hour * 60 + t.minute

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
