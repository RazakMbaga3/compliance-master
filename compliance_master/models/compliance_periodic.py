from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import calendar
import logging

_logger = logging.getLogger(__name__)

FREQUENCY_SEL = [
    ('monthly',   'Monthly'),
    ('quarterly', 'Quarterly'),
    ('annual',    'Annual'),
]

SUBMISSION_STATE = [
    ('future',    'Future'),
    ('pending',   'Pending'),
    ('submitted', 'Submitted'),
    ('late',      'Submitted Late'),
    ('overdue',   'Overdue'),
    ('waived',    'Waived / N/A'),
]

# Quarter → last month number
QUARTER_LAST_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def _quarter(d):
    """Return (year, quarter_number) for a date."""
    return d.year, (d.month - 1) // 3 + 1


def _due_date_for_period(obligation, period_date):
    """
    Compute the actual due date for a given period.
    period_date is the first day of the period (month or quarter).
    """
    due_day = obligation.due_day or 7
    freq    = obligation.frequency

    if freq == 'monthly':
        last = calendar.monthrange(period_date.year, period_date.month)[1]
        day  = min(due_day, last)
        return date(period_date.year, period_date.month, day)

    if freq == 'quarterly':
        qtr   = (period_date.month - 1) // 3 + 1
        month = QUARTER_LAST_MONTH[qtr]
        last  = calendar.monthrange(period_date.year, month)[1]
        day   = min(due_day, last)
        return date(period_date.year, month, day)

    if freq == 'annual':
        due_month = obligation.due_month or 12
        last = calendar.monthrange(period_date.year, due_month)[1]
        day  = min(due_day, last)
        return date(period_date.year, due_month, day)

    return period_date


# ══════════════════════════════════════════════════════════════════════════════
#  Periodic Obligation  (the recurring filing definition)
# ══════════════════════════════════════════════════════════════════════════════

class CompliancePeriodicObligation(models.Model):
    _name        = 'compliance.periodic.obligation'
    _description = 'Periodic Compliance Obligation'
    _inherit     = ['mail.thread']
    _order       = 'frequency, due_day, name'

    name         = fields.Char(string='Obligation',  required=True, tracking=True)
    agency       = fields.Char(string='Agency / Authority', required=True, tracking=True)
    division_id  = fields.Many2one('compliance.division', string='Division', tracking=True)
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    description  = fields.Text(string='Description / Notes')

    # ── Schedule ─────────────────────────────────────────────────────────────
    frequency    = fields.Selection(FREQUENCY_SEL, string='Frequency',
                                    required=True, default='monthly', tracking=True)
    due_day      = fields.Integer(string='Due Day', default=7,
                                  help='Day of the month when the filing is due.')
    due_month    = fields.Integer(string='Due Month (Annual only)', default=12,
                                  help='Month number (1-12) when annual filing is due.')
    start_date   = fields.Date(string='Tracking Start Date', default=fields.Date.today,
                               help='Submissions are generated from this date forward.')

    # ── Responsibility ────────────────────────────────────────────────────────
    responsible_direct_id  = fields.Many2one('res.users', string='Responsible (Direct)',  tracking=True)
    responsible_manager_id = fields.Many2one('res.users', string='Responsible (Manager)', tracking=True)
    responsible_head_id    = fields.Many2one('res.users', string='Responsible (Head)',    tracking=True)

    # ── Notifications ─────────────────────────────────────────────────────────
    notify_days_before = fields.Integer(string='Notify (days before due)', default=3)
    notif_template_id  = fields.Many2one('mail.template', string='Email Template',
                                         domain=[('model', '=', 'compliance.periodic.submission')])

    active = fields.Boolean(default=True)

    # ── Linked submissions ────────────────────────────────────────────────────
    submission_ids = fields.One2many('compliance.periodic.submission', 'obligation_id',
                                     string='Submissions')
    submission_count = fields.Integer(compute='_compute_submission_count', string='Submissions')

    # ── Stats ─────────────────────────────────────────────────────────────────
    last_submission_date  = fields.Date(compute='_compute_stats', store=True,
                                        string='Last Submitted')
    current_period_state  = fields.Selection(SUBMISSION_STATE, compute='_compute_stats',
                                             store=True, string='This Period')

    @api.depends('submission_ids')
    def _compute_submission_count(self):
        for ob in self:
            ob.submission_count = len(ob.submission_ids)

    @api.depends('submission_ids.state', 'submission_ids.submitted_date',
                 'submission_ids.due_date')
    def _compute_stats(self):
        today = date.today()
        for ob in self:
            done = ob.submission_ids.filtered(lambda s: s.state in ('submitted', 'late'))
            dates = [d for d in done.mapped('submitted_date') if d]
            ob.last_submission_date = max(dates) if dates else False

            # Find the submission whose period contains today
            current = ob.submission_ids.filtered(
                lambda s: s.period_start <= today <= (s.period_end or today)
            )
            ob.current_period_state = current[0].state if current else False

    def action_view_submissions(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'Submissions – {self.name}',
            'res_model': 'compliance.periodic.submission',
            'view_mode': 'tree,form',
            'domain': [('obligation_id', '=', self.id)],
            'context': {'default_obligation_id': self.id},
        }

    # ── Bulk generation ───────────────────────────────────────────────────────
    @api.model
    def generate_submissions_for_period(self, target_date=None):
        """
        Called by scheduled action daily.
        For each active obligation, ensure a submission record exists for the
        current period. Creates it as 'pending' if missing.
        Also marks past-due pending submissions as 'overdue'.
        """
        today = target_date or date.today()
        obligations = self.search([('active', '=', True)])
        created = 0

        for ob in obligations:
            period_start, period_end = _period_bounds(ob.frequency, today)
            existing = self.env['compliance.periodic.submission'].search([
                ('obligation_id', '=', ob.id),
                ('period_start',  '=', period_start),
            ], limit=1)

            if not existing:
                due = _due_date_for_period(ob, period_start)
                label = _period_label(ob.frequency, period_start)
                self.env['compliance.periodic.submission'].create({
                    'obligation_id': ob.id,
                    'period_start':  period_start,
                    'period_end':    period_end,
                    'period_label':  label,
                    'due_date':      due,
                    'state':         'pending' if today <= due else 'overdue',
                })
                created += 1

        # Mark overdue: pending submissions whose due_date has passed
        overdue = self.env['compliance.periodic.submission'].search([
            ('state',    '=', 'pending'),
            ('due_date', '<', today),
        ])
        overdue.write({'state': 'overdue'})

        _logger.info('generate_submissions_for_period: %d created, %d marked overdue',
                     created, len(overdue))
        return created


# ══════════════════════════════════════════════════════════════════════════════
#  Periodic Submission  (one filing instance per period)
# ══════════════════════════════════════════════════════════════════════════════

class CompliancePeriodicSubmission(models.Model):
    _name        = 'compliance.periodic.submission'
    _description = 'Periodic Compliance Submission'
    _inherit     = ['mail.thread']
    _order       = 'due_date desc'
    _rec_name    = 'display_name'

    obligation_id = fields.Many2one('compliance.periodic.obligation', string='Obligation',
                                    required=True, ondelete='cascade', tracking=True)

    # ── Period identity ───────────────────────────────────────────────────────
    period_label  = fields.Char(string='Period',  required=True)
    period_start  = fields.Date(string='Period Start', required=True)
    period_end    = fields.Date(string='Period End')
    due_date      = fields.Date(string='Due Date', required=True, tracking=True)

    # ── Convenience relational fields from obligation ─────────────────────────
    agency        = fields.Char(related='obligation_id.agency',      store=True, string='Agency')
    division_id   = fields.Many2one(related='obligation_id.division_id',   store=True)
    department_id = fields.Many2one(related='obligation_id.department_id', store=True)
    frequency     = fields.Selection(related='obligation_id.frequency',    store=True)

    # ── Submission details ────────────────────────────────────────────────────
    state         = fields.Selection(SUBMISSION_STATE, string='Status',
                                     default='pending', tracking=True)
    submitted_date = fields.Date(string='Submitted On', tracking=True)
    submitted_by   = fields.Many2one('res.users', string='Submitted By',
                                     default=lambda self: self.env.user)
    receipt_ref    = fields.Char(string='Receipt / Reference No.', tracking=True)
    amount         = fields.Float(string='Amount (TZS)', digits=(16, 2))
    notes          = fields.Text(string='Notes')

    # ── Days overdue (display) ────────────────────────────────────────────────
    days_overdue   = fields.Integer(compute='_compute_days_overdue', string='Days Overdue')
    display_name   = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('obligation_id.name', 'period_label')
    def _compute_display_name(self):
        for s in self:
            ob_name = s.obligation_id.name or ''
            s.display_name = f'{ob_name} – {s.period_label}' if s.period_label else ob_name

    @api.depends('due_date', 'state', 'submitted_date')
    def _compute_days_overdue(self):
        today = date.today()
        for s in self:
            if s.state in ('submitted', 'late', 'waived', 'future'):
                s.days_overdue = 0
            elif s.due_date:
                delta = (today - s.due_date).days
                s.days_overdue = max(delta, 0)
            else:
                s.days_overdue = 0

    # ── State transitions ─────────────────────────────────────────────────────
    def action_mark_submitted(self):
        today = date.today()
        for s in self:
            new_state = 'late' if today > s.due_date else 'submitted'
            s.write({
                'state':          new_state,
                'submitted_date': today,
                'submitted_by':   self.env.user.id,
            })

    def action_mark_waived(self):
        self.write({'state': 'waived'})

    def action_reset_pending(self):
        today = date.today()
        for s in self:
            s.state = 'overdue' if today > s.due_date else 'pending'

    # ── Notification ─────────────────────────────────────────────────────────
    @api.model
    def send_due_reminders(self):
        """Scheduled daily: notify responsible staff for submissions due soon."""
        today = date.today()
        pending = self.search([('state', 'in', ['pending', 'future'])])
        for sub in pending:
            ob = sub.obligation_id
            if not ob.notify_days_before:
                continue
            days_left = (sub.due_date - today).days
            if 0 <= days_left <= ob.notify_days_before:
                recipients = [
                    ob.responsible_direct_id,
                    ob.responsible_manager_id,
                    ob.responsible_head_id,
                ]
                for user in recipients:
                    if user and user.email:
                        sub.message_post(
                            body=(
                                f'<p>Reminder: <b>{ob.name}</b> is due on '
                                f'<b>{sub.due_date}</b> ({days_left} day(s) left).</p>'
                            ),
                            subject=f'Periodic Filing Due: {ob.name} – {sub.period_label}',
                            partner_ids=[user.partner_id.id],
                            subtype_xmlid='mail.mt_comment',
                        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _period_bounds(frequency, ref_date):
    """Return (period_start, period_end) for the period containing ref_date."""
    if frequency == 'monthly':
        start = ref_date.replace(day=1)
        end   = (start + relativedelta(months=1)) - timedelta(days=1)
        return start, end

    if frequency == 'quarterly':
        q     = (ref_date.month - 1) // 3
        start = ref_date.replace(month=q * 3 + 1, day=1)
        end   = (start + relativedelta(months=3)) - timedelta(days=1)
        return start, end

    if frequency == 'annual':
        start = ref_date.replace(month=1, day=1)
        end   = ref_date.replace(month=12, day=31)
        return start, end

    return ref_date, ref_date


def _period_label(frequency, period_start):
    MONTHS = ['Jan','Feb','Mar','Apr','May','Jun',
              'Jul','Aug','Sep','Oct','Nov','Dec']
    if frequency == 'monthly':
        return f"{MONTHS[period_start.month - 1]} {period_start.year}"
    if frequency == 'quarterly':
        q = (period_start.month - 1) // 3 + 1
        return f"Q{q} {period_start.year}"
    if frequency == 'annual':
        return f"FY {period_start.year}"
    return str(period_start)
