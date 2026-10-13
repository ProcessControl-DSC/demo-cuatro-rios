{
    'name': 'Anthropic Claude for Odoo AI',
    'version': '19.0.1.1.0',
    'summary': 'Adds Anthropic Claude as an LLM provider for Odoo AI (agents, fields, server actions, livechat)',
    'description': """
Anthropic Claude for Odoo AI
============================

Extends Odoo's built-in AI module (enterprise) with Anthropic Claude as a
third LLM provider alongside OpenAI and Google Gemini. Once installed and
an Anthropic API key is entered, Claude models (Opus, Sonnet, Haiku) appear
in every place that exposes a model selection: AI Agents, AI Fields, AI
Server Actions, AI Composer, Livechat bots, and the Knowledge / Documents
/ CRM integrations.

Claude shines at long-form content (blog posts, product descriptions, SEO
metadata) and regional languages. Prompt caching is wired so repeated
system prompts are billed at roughly 90 percent discount on the cached
portion.
""",
    'category': 'Productivity/AI',
    'author': 'Viavista d.o.o.',
    'website': 'https://www.viavista.ba',
    'support': 'info@viavista.ba',
    'license': 'LGPL-3',
    'depends': ['ai', 'ai_app'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'views/res_config_settings_views.xml',
        'data/ai_agent_data.xml',
    ],
    'images': ['images/main_screenshot.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
