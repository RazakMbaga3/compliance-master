from odoo import models, fields, api


class ComplianceDocument(models.Model):
    _name = 'compliance.document'
    _description = 'Compliance Document Version'
    _order = 'version desc'

    compliance_id = fields.Many2one('compliance.record', string='Compliance', required=True, ondelete='cascade')
    version = fields.Integer(string='Version', required=True)
    document_name = fields.Char(string='Document Name', required=True)
    attachment_id = fields.Many2one('ir.attachment', string='File')
    attachment_file = fields.Binary(string='Upload File', attachment=True)
    attachment_filename = fields.Char(string='Filename')
    uploaded_by = fields.Many2one('res.users', string='Uploaded By', default=lambda self: self.env.user)
    upload_date = fields.Date(string='Upload Date', default=fields.Date.today)
    notes = fields.Text(string='Notes')
    is_current = fields.Boolean(string='Current Version', compute='_compute_is_current', store=True)

    @api.depends('compliance_id.current_document_id')
    def _compute_is_current(self):
        for rec in self:
            rec.is_current = rec.compliance_id.current_document_id == rec

    @api.model
    def create(self, vals):
        if 'version' not in vals or not vals['version']:
            compliance = self.env['compliance.record'].browse(vals.get('compliance_id'))
            existing = self.search([('compliance_id', '=', vals.get('compliance_id'))])
            vals['version'] = (max(existing.mapped('version')) + 1) if existing else 1
        return super().create(vals)
