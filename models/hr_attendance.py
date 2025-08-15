from    odoo import _, api, fields, models
from    odoo.tools  import date_utils
from datetime import datetime, timedelta
import logging
_logger = logging.getLogger(__name__)


class SaAttendance(models.Model):
    _inherit = "hr.attendance"

    currency_id         = fields.Many2one('res.currency', 'Currency', store=True, related='employee_id.company_id.currency_id', tracking=True)
    waved               = fields.Boolean(default=False, tracking=True)
    late_minutes        = fields.Integer(readonly=False, pre_compute=True,  store=True, compute="_compute_late_minutes", tracking=True)
    deduction_amount    = fields.Monetary(store=True, pre_compute=True, compute="_compute_deduction", readonly=False, tracking=True)
    display_late_minutes= fields.Float(readonly=False, string="Late", pre_compute=True,  store=True, compute="_compute_late_minutes")


    @api.depends("employee_id", "check_in", "check_out")
    def _compute_late_minutes(self):
        for r in self:
            r.late_minutes = 0
            r.display_late_minutes = 0

            if not r.employee_id or not r.employee_id.resource_calendar_id:
                continue
            if not r.employee_id.tz:
                continue

            # Use check_in if present, else fall back to check_out
            first_punch = r.check_in 
            if not first_punch:
                continue

            # Localize punch time
            punch_dt = date_utils._softatt_localize(first_punch, r.employee_id.tz)
            weekday = str(punch_dt.weekday())  # '0' = Monday

            _logger.info("First punch localized: %s (weekday: %s)", punch_dt, weekday)

            calendar = r.employee_id.resource_calendar_id
            attendances = calendar.attendance_ids.filtered(
                lambda a: a.dayofweek == weekday and a.day_period != 'break'
            )
            if not attendances:
                continue

            # Get all start times for that day, sorted
            start_hours = sorted(attendances.mapped('hour_from'))
            first_slot_start = start_hours[0]
            second_slot_start = start_hours[1] if len(start_hours) > 1 else None

            # Decide expected start time
            punch_hour_decimal = punch_dt.hour + punch_dt.minute / 60.0
            if second_slot_start and punch_hour_decimal >= second_slot_start:
                expected_start_hour = second_slot_start  # morning leave case
            else:
                expected_start_hour = first_slot_start

            # Build expected start datetime
            shift_start_dt = datetime.combine(punch_dt.date(), datetime.min.time()) + timedelta(hours=expected_start_hour)

            _logger.info("Expected shift start time: %s", shift_start_dt)

            # Compute lateness
            diff_minutes = (punch_dt - shift_start_dt).total_seconds() / 60
            r.late_minutes = max(0, int(diff_minutes))
            r.display_late_minutes = round(r.late_minutes / 60.0, 2)



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
                r.deduction_amount=0.0
                return
            r.deduction_amount =  r.employee_id.attendance_rule_id._compute_deduction(r.employee_id, r.check_in, r.late_minutes)
            
    def get_compute_deduction(self):
        for r in self:
            r._compute_deduction()
            
    def action_wave_deduction(self):
        for r in self:
            r.waved = True
            
    def action_unwave_deduction(self):
        for r in self:
            r.waved = False