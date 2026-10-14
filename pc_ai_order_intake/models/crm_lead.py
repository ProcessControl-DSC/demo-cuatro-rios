# Part of Process Control. License LGPL-3.
import logging

from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

HISTORY_LENGTH = 12


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    messaging_ref = fields.Char(
        string="Messaging chat", index='btree_not_null', copy=False, readonly=True,
        help="Channel and chat of the messaging app this conversation comes from (e.g. telegram:12345).")

    @api.model
    def _messaging_receive(self, channel, chat_id, sender_name, text, phone=None):
        """Handle one message of a messaging app (Telegram...) relayed by an
        integration such as n8n. The conversation lives in an opportunity of
        the orders team; the agent answers locked to the customer identified
        by the phone the user shared.

        :return: dict with ``need_contact`` when the user must share the
            phone first, otherwise ``reply`` (text for the user) and
            ``quotations`` (name and portal link of each draft quotation)
        """
        channel = (channel or 'chat').strip().lower()
        ref = '%s:%s' % (channel, chat_id)
        team = self.env.ref('pc_ai_order_intake.crm_team_orders')
        lead = self.search([('messaging_ref', '=', ref)], limit=1)
        if not lead:
            tags = self.env.ref('pc_ai_order_intake.crm_tag_ai_order')
            channel_tag = self.env.ref('pc_ai_order_intake.crm_tag_channel_%s' % channel,
                                       raise_if_not_found=False) or tags.browse()
            tags |= channel_tag
            # the channel tag carries the label in the company language (Pedido IA: teléfono)
            lang = (team.company_id or self.env.company).partner_id.lang
            label = channel_tag.with_context(lang=lang).name or channel.capitalize()
            lead = self.create({
                'name': '%s · %s' % (label, sender_name or chat_id),
                'team_id': team.id,
                'messaging_ref': ref,
                'tag_ids': [(6, 0, tags.ids)],
            })
        if phone and not lead.partner_id:
            digits = ''.join(c for c in phone if c.isdigit())[-9:]
            if len(digits) == 9:
                partner = self.env['res.partner'].search([('phone_sanitized', 'like', digits)], limit=1)
                if partner:
                    lead.partner_id = partner
        if text:
            lead.message_post(body=text, author_id=lead.partner_id.id or False,
                              message_type='comment', subtype_xmlid='mail.mt_note')
        if not lead.partner_id:
            # first the customer identifies himself with the share-phone button
            return {'need_contact': True, 'phone_unknown': bool(phone)}

        customer = lead.partner_id.commercial_partner_id
        notes = self.env['mail.message'].search([
            ('model', '=', 'crm.lead'), ('res_id', '=', lead.id), ('message_type', '=', 'comment'),
        ], order='id desc', limit=HISTORY_LENGTH).sorted('id')
        company_partner = (lead.company_id or self.env.company).partner_id
        conversation = '\n'.join(
            '%s: %s' % ('Us' if note.author_id == company_partner else 'Customer',
                        html2plaintext(note.body or '').strip())
            for note in notes)
        quotations = self.env['sale.order'].search([('opportunity_id', '=', lead.id)])
        if channel == 'phone':
            style = (
                'Your answer is read aloud to the customer on the phone by a voice: two or three short '
                'sentences, no lists, no links, no emojis, no symbols. Give the quotation number exactly as '
                'written (S00088), never spelled out, and the total, and ask only about what you could not '
                'match. When you create a quotation, pass channel=phone.')
        else:
            style = (
                'Your answer is sent as it is in the chat: plain text, no Markdown and no links: the chat '
                'adds buttons to view the quotation and the weekly offer, and shortcuts for a new order, '
                'the usual order and the offer, so do not list those options yourself. When you create a '
                'quotation, pass channel=%s.' % channel)
        prompt = (
            'Channel: %(channel)s (conversational)\n'
            'Customer recognised by the phone: %(customer)s (partner_id %(customer_id)s)\n'
            'lead_id: %(lead_id)s\n'
            'Quotations already created in this conversation: %(quotations)s\n\n'
            'Recent conversation, oldest first:\n%(conversation)s\n\n'
            'Answer the customer\'s last message, always briefly. %(style)s'
        ) % {
            'style': style,
            'channel': channel, 'customer': customer.name, 'customer_id': customer.id,
            'lead_id': lead.id, 'conversation': conversation or '-',
            'quotations': ', '.join('%s (%s)' % (q.name, q.state) for q in quotations) or 'none',
        }
        agent = self.env.ref('pc_ai_order_intake.ai_agent_order_intake')
        try:
            reply = '\n'.join(agent.with_context(ai_order_partner_id=customer.id).get_direct_response(prompt))
        except Exception:
            _logger.exception("AI order intake failed on %s", ref)
            reply = ''
        reply = reply.strip()
        if reply:
            lead.message_post(body=Markup('<br/>').join(reply.split('\n')), author_id=company_partner.id,
                              message_type='comment', subtype_xmlid='mail.mt_note')
        quotations = self.env['sale.order'].search([('opportunity_id', '=', lead.id), ('state', 'in', ('draft', 'sent'))])
        return {
            'reply': reply,
            'customer': customer.name,
            'offers_url': self.env['ir.config_parameter'].sudo().get_param('pc_ai_order_intake.offers_url') or '',
            'quotations': [{'name': q.name, 'url': q.get_base_url() + q.get_portal_url()} for q in quotations],
        }
