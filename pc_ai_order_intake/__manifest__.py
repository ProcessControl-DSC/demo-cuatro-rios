# Part of Process Control. License LGPL-3.
{
    'name': 'AI order intake (email, WhatsApp and website chat)',
    'version': '19.0.1.0.6',
    'summary': 'An Odoo AI agent turns customer orders received by email, WhatsApp '
               'or the website chat into draft quotations',
    'category': 'Sales/Sales',
    'author': 'Process Control',
    'website': 'https://www.processcontrol.es',
    'license': 'LGPL-3',
    'depends': ['ai_app', 'ai_livechat', 'sale_crm', 'whatsapp', 'website_livechat'],
    'data': [
        'data/crm_data.xml',
        'data/ai_tools.xml',
        'data/ai_agent_data.xml',
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': False,
    'description': """
<div style="font-family:Montserrat,Arial,sans-serif;color:#16294C;max-width:900px">
<h2 style="color:#16294C;border-bottom:3px solid #1B72D3;padding-bottom:6px">Toma de pedidos con IA</h2>
<p><b>Contexto:</b> los clientes piden por correo, por WhatsApp y por el chat de la web, cada uno a su manera.
Teclear esos pedidos ocupa a una o dos personas cada día. Este módulo pone un agente de IA de Odoo a leerlos
y a dejarlos como presupuesto en borrador, listo para que el equipo lo valide.</p>

<h3 style="color:#1B72D3">Qué hace</h3>
<ul>
<li>Reconoce al cliente por su correo, su teléfono o el nombre del negocio.</li>
<li>Entiende los productos con las palabras del cliente: busca por nombre, por referencia y por los nombres
alternativos escritos en las notas internas del producto.</li>
<li>Repite el último pedido cuando el cliente pide «lo de siempre».</li>
<li>Crea el presupuesto en borrador con sus unidades (kg, unidades, cajas) y la tarifa del cliente.</li>
<li>Contesta por el mismo canal con un resumen y pide confirmación; cuando el cliente confirma, marca el
presupuesto con la etiqueta «Confirmed by the customer».</li>
<li>Sugiere, en una frase, un producto que compran clientes parecidos.</li>
<li>Nunca confirma el pedido: siempre lo valida una persona.</li>
</ul>

<h3 style="color:#1B72D3">Canales</h3>
<ul>
<li><b>Correo:</b> los correos al alias del equipo de ventas «Orders by email» crean una oportunidad.
Un cron la entrega al agente, que crea el presupuesto enlazado a la oportunidad y contesta al cliente.</li>
<li><b>WhatsApp:</b> requiere la cuenta de WhatsApp Business configurada en Odoo. Un cron lee los mensajes
entrantes, pasa la conversación reciente al agente y responde por el mismo chat.</li>
<li><b>Chat de la web:</b> la regla del canal de Live Chat asigna el agente al chat de la web.</li>
</ul>

<h3 style="color:#1B72D3">Configuración</h3>
<ol>
<li>IA → Agentes → «Order assistant»: elegir el modelo de lenguaje y adaptar las instrucciones a la empresa
(qué vende, cómo se llaman sus formatos, tono).</li>
<li>Productos: escribir en las notas internas los nombres con los que los piden los clientes.</li>
<li>Unidades: crear las cajas como unidades relativas al kg (por ejemplo «Caja 5 kg», factor 5).</li>
<li>CRM → Configuración → Equipos de ventas → «Orders by email»: revisar el alias de correo.</li>
<li>WhatsApp → Configuración → Cuentas: conectar la cuenta de Meta.</li>
</ol>

<h3 style="color:#1B72D3">Límites conocidos</h3>
<ul>
<li>Las notas de voz y las imágenes no se interpretan: el agente pide que se escriba el pedido.</li>
<li>Los PDF adjuntos al correo se leen a partir del texto que extrae la indexación de adjuntos; un PDF
escaneado sin texto no se lee.</li>
<li>Los crons se ejecutan cada minuto: la respuesta tarda hasta un minuto más lo que tarde el modelo.</li>
</ul>
</div>
""",
}
