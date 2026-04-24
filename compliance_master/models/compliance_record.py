from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date, timedelta
import logging

_logger = logging.getLogger(__name__)


class ComplianceRecord(models.Model):
    _name = 'compliance.record'
    _description = 'Compliance Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'expiry_date asc'

    name = fields.Char(string='Description / Details', required=True, tracking=True)
    ref = fields.Char(string='Reference No.', readonly=True, default='New')
    agency = fields.Char(string='Agency / Institution / Govt Body', required=True, tracking=True)
    division = fields.Char(string='Division', tracking=True)
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    location_custodian = fields.Char(string='Location / Custodian', tracking=True)
    frequency = fields.Selection([
        ('one_time', 'One Time'),
        ('annual', 'Annual'),
        ('bi_annual', 'Bi-Annual'),
        ('quarterly', 'Quarterly'),
        ('monthly', 'Monthly'),
        ('5_yrs', '5 Years'),
        ('10_yrs', '10 Years'),
        ('15_yrs', '15 Years'),
        ('other', 'Other'),
    ], string='Frequency', tracking=True)
    frequency_other = fields.Char(string='Other Frequency')

    # Responsibility
    responsible_direct_id = fields.Many2one('res.users', string='Responsible (Direct / Level 1)', tracking=True)
    responsible_manager_id = fields.Many2one('res.users', string='Responsible (Manager / Level 2)', tracking=True)
    responsible_head_id = fields.Many2one('res.users', string='Responsible (Head)', tracking=True)

    # Validity
    origin_date = fields.Date(string='Origin Date', tracking=True)
    valid_from = fields.Date(string='Valid From', tracking=True)
    expiry_date = fields.Date(string='Expiry Date / Valid To', required=True, tracking=True)
    renewal_date = fields.Date(string='Renewal Date', tracking=True)

    # Notification thresholds (days before expiry)
    notify_direct_days = fields.Integer(string='Notify Direct (days before)', default=30)
    notify_manager_days = fields.Integer(string='Notify Manager (days before)', default=20)
    notify_head_days = fields.Integer(string='Notify Head (days before)', default=20)

    remarks = fields.Text(string='Remarks')

    # Document versioning
    document_ids = fields.One2many('compliance.document', 'compliance_id', string='Documents & Versions')
    document_count = fields.Integer(compute='_compute_document_count', string='Documents')
    current_document_id = fields.Many2one('compliance.document', string='Current Document',
                                          compute='_compute_current_document', store=True)

    # Status
    state = fields.Selection([
        ('active', 'Active'),
        ('due', 'Due for Renewal'),
        ('under_renewal', 'Under Renewal'),
        ('overdue', 'Overdue'),
        ('inactive', 'Inactive'),
    ], string='Status', default='active', tracking=True, compute='_compute_state', store=True)

    # Notification tracking
    notified_direct = fields.Boolean(default=False)
    notified_manager = fields.Boolean(default=False)
    notified_head = fields.Boolean(default=False)

    @api.model
    def create(self, vals):
        if vals.get('ref', 'New') == 'New':
            vals['ref'] = self.env['ir.sequence'].next_by_code('compliance.record') or 'New'
        return super().create(vals)

    @api.depends('document_ids')
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @api.depends('document_ids', 'document_ids.version')
    def _compute_current_document(self):
        for rec in self:
            docs = rec.document_ids.sorted('version', reverse=True)
            rec.current_document_id = docs[0] if docs else False

    @api.depends('expiry_date', 'state')
    def _compute_state(self):
        today = date.today()
        for rec in self:
            if rec.state == 'inactive':
                continue
            if rec.state == 'under_renewal':
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

    def action_set_under_renewal(self):
        self.state = 'under_renewal'

    def action_set_active(self):
        self.write({
            'state': 'active',
            'notified_direct': False,
            'notified_manager': False,
            'notified_head': False,
        })

    def action_set_inactive(self):
        self.state = 'inactive'

    def action_view_documents(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documents',
            'res_model': 'compliance.document',
            'view_mode': 'tree,form',
            'domain': [('compliance_id', '=', self.id)],
            'context': {'default_compliance_id': self.id},
        }

    @api.model
    def send_renewal_reminders(self):
        """Scheduled action: send email reminders based on per-record thresholds."""
        today = date.today()
        records = self.search([('state', 'not in', ['inactive']), ('expiry_date', '!=', False)])
        template_direct = self.env.ref('compliance_master.email_template_renewal_direct', raise_if_not_found=False)
        template_manager = self.env.ref('compliance_master.email_template_renewal_manager', raise_if_not_found=False)
        template_head = self.env.ref('compliance_master.email_template_renewal_head', raise_if_not_found=False)

        for rec in records:
            days_left = (rec.expiry_date - today).days
            if days_left < 0:
                days_left_display = days_left  # negative = overdue

            # Notify Direct / Level 1
            if (rec.responsible_direct_id and not rec.notified_direct
                    and rec.notify_direct_days > 0
                    and 0 <= days_left <= rec.notify_direct_days):
                if template_direct:
                    template_direct.send_mail(rec.id, force_send=True,
                                              email_values={'email_to': rec.responsible_direct_id.email})
                    rec.notified_direct = True
                    _logger.info('Renewal reminder (Direct) sent for %s', rec.name)

            # Notify Manager / Level 2
            if (rec.responsible_manager_id and not rec.notified_manager
                    and rec.notify_manager_days > 0
                    and 0 <= days_left <= rec.notify_manager_days):
                if template_manager:
                    template_manager.send_mail(rec.id, force_send=True,
                                               email_values={'email_to': rec.responsible_manager_id.email})
                    rec.notified_manager = True
                    _logger.info('Renewal reminder (Manager) sent for %s', rec.name)

            # Notify Head
            if (rec.responsible_head_id and not rec.notified_head
                    and rec.notify_head_days > 0
                    and 0 <= days_left <= rec.notify_head_days):
                if template_head:
                    template_head.send_mail(rec.id, force_send=True,
                                            email_values={'email_to': rec.responsible_head_id.email})
                    rec.notified_head = True
                    _logger.info('Renewal reminder (Head) sent for %s', rec.name)

            # Also notify overdue (once)
            if days_left < 0:
                if rec.responsible_direct_id and not rec.notified_direct:
                    if template_direct:
                        template_direct.send_mail(rec.id, force_send=True,
                                                  email_values={'email_to': rec.responsible_direct_id.email})
                        rec.notified_direct = True
                if rec.responsible_head_id and not rec.notified_head:
                    if template_head:
                        template_head.send_mail(rec.id, force_send=True,
                                                email_values={'email_to': rec.responsible_head_id.email})
                        rec.notified_head = True
