from odoo import models, fields, api
from datetime import date
import logging

_logger = logging.getLogger(__name__)

EXCEL_STATUS_MAP = {
    'active': 'active', 'valid': 'active', 'available': 'active',
    '√': 'active', 'renewed': 'active', 'submitted yearly': 'active',
    'submitted quarterly': 'active', '90 days': 'active', 'yes': 'active',
    'under renewal': 'under_renewal', 'under renewal fees paid': 'under_renewal',
    'on process': 'under_renewal', 'under renewal fees': 'under_renewal',
    'not valid': 'overdue', 'expired': 'overdue',
    'inactive': 'inactive', 'not applied': 'inactive',
    'due': 'due',
}


def map_excel_status(raw):
    if not raw:
        return 'active'
    return EXCEL_STATUS_MAP.get(str(raw).strip().lower(), 'active')


class ComplianceRecord(models.Model):
    _name = 'compliance.record'
    _description = 'Compliance Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'expiry_date asc nulls last'

    # ── Identity ──────────────────────────────────────────────────────────────
    name = fields.Char(string='Description / Details', required=True, tracking=True)
    ref = fields.Char(string='Reference No.', readonly=True, default='New', copy=False)
    certificate_number = fields.Char(string='Certificate / Licence No.', tracking=True)

    # ── Classification ────────────────────────────────────────────────────────
    compliance_type = fields.Selection([
        ('license',     'Licence / Permit'),
        ('certificate', 'Certificate'),
        ('fleet',       'Fleet / Vehicle'),
        ('periodic',    'Periodic Submission'),
        ('other',       'Other'),
    ], string='Compliance Type', required=True, default='license', tracking=True)

    division_id = fields.Many2one('compliance.division', string='Division', tracking=True,
                                  ondelete='restrict')
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    agency = fields.Char(string='Agency / Institution / Govt Body', required=True, tracking=True)
    location_custodian = fields.Char(string='Location / Custodian', tracking=True)

    # ── Fleet / Vehicle ───────────────────────────────────────────────────────
    vehicle_reg = fields.Char(string='Vehicle Reg. No.', tracking=True)
    vehicle_id = fields.Many2one(
        'compliance.vehicle', string='Vehicle',
        tracking=True, ondelete='set null',
    )

    # ── Frequency ─────────────────────────────────────────────────────────────
    frequency = fields.Selection([
        ('one_time',   'One Time'),
        ('monthly',    'Monthly'),
        ('quarterly',  'Quarterly'),
        ('bi_annual',  'Bi-Annual'),
        ('annual',     'Annual'),
        ('2_yrs',      '2 Years'),
        ('5_yrs',      '5 Years'),
        ('10_yrs',     '10 Years'),
        ('15_yrs',     '15 Years'),
        ('lifetime',   'Lifetime / Project'),
        ('other',      'Other'),
    ], string='Frequency', tracking=True)
    frequency_other = fields.Char(string='Other Frequency')

    # ── Responsibility ────────────────────────────────────────────────────────
    responsible_direct_id  = fields.Many2one('res.users', string='Responsible – Direct (Level 1)', tracking=True)
    responsible_manager_id = fields.Many2one('res.users', string='Responsible – Manager (Level 2)', tracking=True)
    responsible_head_id    = fields.Many2one('res.users', string='Responsible – Head', tracking=True)

    # ── Validity ──────────────────────────────────────────────────────────────
    origin_date  = fields.Date(string='Origin Date', tracking=True)
    valid_from   = fields.Date(string='Valid From', tracking=True)
    expiry_date  = fields.Date(string='Expiry Date / Valid To', tracking=True)
    renewal_date = fields.Date(string='Renewal Date', tracking=True)
    due_day_text = fields.Char(string='Due On (periodic)')

    # ── Notification thresholds ───────────────────────────────────────────────
    notify_direct_days  = fields.Integer(string='Notify Direct (days before)',  default=30)
    notify_manager_days = fields.Integer(string='Notify Manager (days before)', default=20)
    notify_head_days    = fields.Integer(string='Notify Head (days before)',    default=20)

    remarks = fields.Text(string='Remarks')

    # ── Documents ─────────────────────────────────────────────────────────────
    document_ids        = fields.One2many('compliance.document', 'compliance_id', string='Documents & Versions')
    document_count      = fields.Integer(compute='_compute_document_count', string='Documents')
    current_document_id = fields.Many2one('compliance.document', string='Current Document',
                                          compute='_compute_current_document', store=True)

    # ── Status (plain field — updated by _auto_update_state) ─────────────────
    state = fields.Selection([
        ('active',        'Active'),
        ('due',           'Due for Renewal'),
        ('under_renewal', 'Under Renewal'),
        ('overdue',       'Overdue'),
        ('inactive',      'Inactive'),
    ], string='Status', default='active', tracking=True)

    # ── Notification tracking ─────────────────────────────────────────────────
    notified_direct  = fields.Boolean(default=False)
    notified_manager = fields.Boolean(default=False)
    notified_head    = fields.Boolean(default=False)

    # ── Days remaining ────────────────────────────────────────────────────────
    days_to_expiry = fields.Integer(string='Days to Expiry', compute='_compute_days_to_expiry')

    # ══════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ══════════════════════════════════════════════════════════════════════════

    @api.model
    def create(self, vals):
        if vals.get('ref', 'New') == 'New':
            vals['ref'] = self.env['ir.sequence'].next_by_code('compliance.record') or 'New'
        rec = super().create(vals)
        rec._auto_update_state()
        return rec

    def write(self, vals):
        result = super().write(vals)
        if 'expiry_date' in vals:
            today = date.today()
            for rec in self:
                rec._auto_update_state()
                if rec.expiry_date and rec.expiry_date > today:
                    self.env.cr.execute(
                        "UPDATE compliance_record SET notified_direct=false,"
                        "notified_manager=false,notified_head=false WHERE id=%s",
                        (rec.id,)
                    )
                    rec.invalidate_cache(fnames=['notified_direct', 'notified_manager', 'notified_head'])
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # Computed fields
    # ══════════════════════════════════════════════════════════════════════════

    @api.depends('document_ids')
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @api.depends('document_ids', 'document_ids.version')
    def _compute_current_document(self):
        for rec in self:
            docs = rec.document_ids.sorted('version', reverse=True)
            rec.current_document_id = docs[0] if docs else False

    @api.depends('expiry_date')
    def _compute_days_to_expiry(self):
        today = date.today()
        for rec in self:
            rec.days_to_expiry = (rec.expiry_date - today).days if rec.expiry_date else 0

    def _auto_update_state(self):
        """Auto-set state from expiry date. Skips manually locked states."""
        today = date.today()
        for rec in self:
            if rec.state in ('inactive', 'under_renewal'):
                continue
            if not rec.expiry_date:
                rec.state = 'active'
                continue
            days_left = (rec.expiry_date - today).days
            if days_left < 0:
                rec.state = 'overdue'
            elif days_left <= 30:
                rec.state = 'due'
            else:
                rec.state = 'active'

    # ══════════════════════════════════════════════════════════════════════════
    # Button actions
    # ══════════════════════════════════════════════════════════════════════════

    def action_set_under_renewal(self):
        self.write({'state': 'under_renewal'})

    def action_set_active(self):
        self.write({
            'notified_direct': False,
            'notified_manager': False,
            'notified_head': False,
        })
        self._auto_update_state()

    def action_set_inactive(self):
        self.write({'state': 'inactive'})

    def action_view_documents(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documents',
            'res_model': 'compliance.document',
            'view_mode': 'tree,form',
            'domain': [('compliance_id', '=', self.id)],
            'context': {'default_compliance_id': self.id},
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Scheduled action — daily renewal reminders
    # ══════════════════════════════════════════════════════════════════════════

    @api.model
    def send_renewal_reminders(self):
        today = date.today()
        records = self.search([
            ('state', 'not in', ['inactive']),
            ('expiry_date', '!=', False),
        ])
        tmpl_direct  = self.env.ref('compliance_master.email_template_renewal_direct',  raise_if_not_found=False)
        tmpl_manager = self.env.ref('compliance_master.email_template_renewal_manager', raise_if_not_found=False)
        tmpl_head    = self.env.ref('compliance_master.email_template_renewal_head',    raise_if_not_found=False)

        for rec in records:
            days_left = (rec.expiry_date - today).days

            if rec.responsible_direct_id and not rec.notified_direct and rec.notify_direct_days > 0:
                if 0 <= days_left <= rec.notify_direct_days or days_left < 0:
                    if tmpl_direct:
                        tmpl_direct.send_mail(rec.id, force_send=True,
                                              email_values={'email_to': rec.responsible_direct_id.email})
                        rec.notified_direct = True
                        _logger.info('Reminder (Direct) sent: %s', rec.name)

            if rec.responsible_manager_id and not rec.notified_manager and rec.notify_manager_days > 0:
                if 0 <= days_left <= rec.notify_manager_days:
                    if tmpl_manager:
                        tmpl_manager.send_mail(rec.id, force_send=True,
                                               email_values={'email_to': rec.responsible_manager_id.email})
                        rec.notified_manager = True
                        _logger.info('Reminder (Manager) sent: %s', rec.name)

            if rec.responsible_head_id and not rec.notified_head and rec.notify_head_days > 0:
                if 0 <= days_left <= rec.notify_head_days or days_left < 0:
                    if tmpl_head:
                        tmpl_head.send_mail(rec.id, force_send=True,
                                            email_values={'email_to': rec.responsible_head_id.email})
                        rec.notified_head = True
                        _logger.info('Reminder (Head) sent: %s', rec.name)
