{
    'name': 'Compliance Master',
    'version': '15.0.2.0.0',
    'category': 'Compliance',
    'summary': 'Track licences, certificates and regulatory compliances with automated renewal reminders',
    'author': 'Lake Cement Limited',
    'depends': ['base', 'mail', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/divisions.xml',
        'data/mail_templates.xml',
        'data/scheduled_actions.xml',
        'views/compliance_division_views.xml',
        'views/compliance_record_views.xml',
        'views/compliance_document_views.xml',
        'views/compliance_dashboard_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'compliance_master/static/src/css/dashboard.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
