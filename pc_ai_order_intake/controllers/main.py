# Part of Process Control. License LGPL-3.
from odoo import http
from odoo.http import request
from odoo.tools import consteq


class OrderIntakeMessaging(http.Controller):

    @http.route('/pc_ai_order_intake/messaging', type='http', methods=['POST'], auth='public', csrf=False)
    def messaging(self, **kwargs):
        """Entry point for messaging apps relayed by an integration (n8n...).
        Disabled until the system parameter pc_ai_order_intake.messaging_token
        is set; the caller sends it in the X-Order-Intake-Token header."""
        expected = request.env['ir.config_parameter'].sudo().get_param('pc_ai_order_intake.messaging_token')
        given = request.httprequest.headers.get('X-Order-Intake-Token') or ''
        if not expected or not consteq(given, expected):
            return request.make_json_response({'error': 'forbidden'}, status=403)
        data = request.get_json_data()
        chat_id = str(data.get('chat_id') or '').strip()
        if not chat_id:
            return request.make_json_response({'error': 'chat_id is required'}, status=400)
        # sudo: the caller is a trusted integration (token); the agent itself stays
        # locked to the customer identified in this chat
        result = request.env['crm.lead'].sudo()._messaging_receive(
            data.get('channel') or 'chat', chat_id, data.get('sender_name') or '',
            (data.get('text') or '').strip(), phone=data.get('phone'))
        return request.make_json_response(result)
