import { External, Mail, NoticeTable, type PrivacyContent } from "./privacy-notice";

export const privacyEs: PrivacyContent = {
  lang: "es",
  title: "Aviso de privacidad para candidatos",
  updated: "Última actualización: 21 de octubre de 2026",
  intro: (
    <p>
      Este aviso explica qué hacemos con tus datos cuando te postulas a un puesto de repartidor/a en Grupo Sazón a través
      de nuestro chat de selección. Cumple el Reglamento General de Protección de Datos (RGPD) y la LOPDGDD en España, y la
      Ley Federal de Protección de Datos Personales en Posesión de los Particulares (LFPDPPP) en México.
    </p>
  ),
  summaryTitle: "En pocas palabras",
  summary: [
    <>
      El chat lo atiende <strong>Ana, una asistente de inteligencia artificial</strong>, no una persona.
    </>,
    "Solo empieza si respondes que sí al aviso. Si respondes que no, el chat termina y no guardamos tus datos ni la conversación.",
    "Ana cierra una candidatura por sí sola en pocos casos: si no tienes carnet o licencia de conducir en vigor, si vives fuera de nuestras zonas de reparto, si no consigue tu nombre, el carnet, tu ciudad o una forma de contactarte (puedes empezar de nuevo), o si se intenta manipularla. Tú puedes dejarlo cuando quieras.",
    "Las decisiones de contratación las toma siempre el equipo de selección de Grupo Sazón, y puedes pedir que una persona revise cualquier decisión de Ana.",
    "Guardamos tu candidatura un máximo de 12 meses y puedes pedir que la borremos cuando quieras.",
  ],
  tocTitle: "Contenido",
  sections: [
    {
      id: "responsable",
      title: "Quién es responsable de tus datos",
      body: (
        <>
          <p>El responsable depende del país del puesto al que te postulas:</p>
          <NoticeTable
            head={["País", "Responsable"]}
            rows={[
              [
                "España",
                <>
                  Grupo Sazón Restauración, S.L., CIF B-00000000, calle del Pimentón 14, 28012 Madrid.
                </>,
              ],
              [
                "México",
                <>
                  Grupo Sazón México, S.A. de C.V., RFC GSM000000AA0, avenida de los Insurgentes Sur 000, colonia Roma
                  Norte, alcaldía Cuauhtémoc, 06700 Ciudad de México.
                </>,
              ],
            ]}
          />
          <p>
            Para cualquier cuestión sobre privacidad, en los dos países: <Mail />. Desde esa dirección también te atiende
            nuestro delegado de protección de datos.
          </p>
        </>
      ),
    },
    {
      id: "datos",
      title: "Qué datos tratamos",
      body: (
        <>
          <ul>
            <li>
              <strong>Identificación y contacto:</strong> nombre completo, teléfono móvil y correo electrónico. Basta con el teléfono o el correo; sin correo no podemos enviarte la invitación de la entrevista, que verás solo en el chat.
            </li>
            <li>
              <strong>Respuestas de la selección:</strong> si tienes carnet o licencia de conducir, tu ciudad o zona y el
              local que te corresponde, tu disponibilidad, el horario que prefieres, tu experiencia en reparto y las
              plataformas en las que has trabajado, y cuándo podrías empezar.
            </li>
            <li>
              <strong>La conversación:</strong> los mensajes que intercambias con Ana y, si la reservas, la cita de
              entrevista.
            </li>
            <li>
              <strong>Si haces la entrevista por voz:</strong> tu voz, que se convierte en texto durante la llamada. Guardamos
              la transcripción como cualquier otra conversación, no la grabación.
            </li>
            <li>
              <strong>Datos técnicos:</strong> el país y el local de la oferta desde la que entras, y el idioma y la zona
              horaria de tu navegador, que usamos para elegir el idioma y proponerte horarios en tu hora local.
            </li>
          </ul>
          <p>
            No te pedimos datos sensibles, como salud, religión u origen étnico. Si los escribes en el chat, no los usamos
            para la selección.
          </p>
        </>
      ),
    },
    {
      id: "finalidades",
      title: "Para qué los usamos y con qué base",
      body: (
        <>
          <NoticeTable
            head={["Finalidad", "Base legal"]}
            rows={[
              [
                "Gestionar tu candidatura: la conversación de selección, la entrevista y el contacto sobre ella.",
                <>
                  España: tu consentimiento, que das con el botón «Sí» al empezar (art. 6.1.a RGPD), y la aplicación de
                  medidas precontractuales a petición tuya (art. 6.1.b). México: finalidad primaria, con tu consentimiento
                  expreso.
                </>,
              ],
              [
                "Guardar tus datos en nuestra bolsa de candidatos para futuras vacantes, solo si lo aceptas en el chat.",
                <>España: tu consentimiento (art. 6.1.a RGPD). México: finalidad secundaria.</>,
              ],
              [
                "Mejorar a Ana revisando conversaciones en las que no entendió bien una respuesta, y elaborar estadísticas agregadas del proceso.",
                <>
                  España: nuestro interés legítimo en que el proceso funcione bien (art. 6.1.f RGPD). México: finalidad
                  secundaria.
                </>,
              ],
            ]}
          />
          <p>
            En México puedes negarte a las finalidades secundarias en cualquier momento escribiendo a <Mail />; negarte no
            afecta a tu candidatura.
          </p>
        </>
      ),
    },
    {
      id: "ia",
      title: "Asistente de IA y decisiones automatizadas",
      body: (
        <>
          <p>
            Ana es un sistema de inteligencia artificial y te lo dice en su primer mensaje, como exige el artículo 50 del
            Reglamento Europeo de Inteligencia Artificial. Aplicamos el mismo criterio en México. Ana hace las preguntas de
            la selección, resuelve dudas frecuentes sobre el puesto y te ofrece horarios de entrevista.
          </p>
          <p>
            Ese reglamento considera de <strong>alto riesgo</strong> los sistemas de IA que se usan para seleccionar
            personal, y este es uno de ellos: te informamos de que en esta selección se usa un sistema de IA de alto riesgo
            (artículo 26.11).
          </p>
          <p>
            Ana cierra una candidatura de forma <strong>automática</strong> solo en estos casos:
          </p>
          <ul>
            <li>
              <strong>No tienes un carnet o licencia de conducir vigente</strong> en el momento de aplicar (no cuenta si lo
              estás sacando, si tienes un permiso de aprendiz o si está caducado, en renovación o suspendido). Puedes
              volver a postularte cuando lo tengas.
            </li>
            <li>
              <strong>Vives fuera de todas nuestras zonas de reparto</strong>: antes de cerrar, Ana te pregunta si es ahí
              donde vives ahora. Puede ofrecerte guardar tus datos en la bolsa de candidatos, solo si aceptas.
            </li>
            <li>
              <strong>Falta un dato imprescindible</strong>: tras varios intentos no ha podido confirmar tu nombre y
              apellido, si tienes carnet, tu ciudad, o al menos un teléfono o un correo para contactarte. Te dice qué
              faltó y puedes empezar de nuevo cuando quieras con el botón «Empezar de nuevo».
            </li>
            <li>
              <strong>Un intento de manipularla</strong>: instrucciones dirigidas al sistema en lugar de respuestas, ocultas
              en caracteres invisibles o codificadas (por ejemplo, en código Morse o en runas). Ana solo te dice que no puede
              continuar con la solicitud. Puedes volver a postularte, con un aviso de que otro intento la cerrará de nuevo;
              tras tres cierres por este motivo con el mismo teléfono o correo, ya no se puede abrir otra solicitud con
              esos datos.
            </li>
          </ul>
          <p>
            En cada caso te explica el motivo en el mismo chat. Tu disponibilidad, tu horario, tu experiencia o la fecha en
            que puedes empezar nunca cierran una candidatura: si no quieres responder a alguna de esas preguntas, se queda
            sin ese dato. Si insultas por segunda vez, Ana termina la conversación.
          </p>
          <p>
            También puedes <strong>dejarlo tú</strong> en cualquier momento: si escribes que no quieres seguir, Ana se
            despide y la candidatura queda cerrada. Lo que ya respondiste se conserva como el resto de candidaturas (ver
            «Cuánto tiempo los guardamos») y puedes pedir que lo borremos.
          </p>
          <p>
            Ana no decide a quién se contrata. Prepara un resumen de tus respuestas para el equipo de selección de Grupo
            Sazón, que toma todas las decisiones de contratación tras la entrevista.
          </p>
          <p>
            Si Ana cierra tu candidatura por cualquiera de estos motivos, tienes derecho a <strong>obtener la intervención de una persona</strong> del
            equipo de selección, a <strong>expresar tu punto de vista</strong> y a <strong>impugnar la decisión</strong>{" "}
            (artículo 22.3 del RGPD). Escribe a <Mail />: una persona revisa tu caso y puede seguir adelante con tu candidatura
            aunque Ana la haya cerrado, también si la cerró por un intento de manipulación: el equipo de selección puede
            cambiar cualquier resultado de Ana. También puedes preguntarnos cómo funciona este tratamiento automatizado u
            oponerte a él.
          </p>
        </>
      ),
    },
    {
      id: "rechazo",
      title: "Si no aceptas el aviso",
      body: (
        <p>
          Si respondes que no al empezar, el chat termina y no guardamos tu nombre, tus respuestas ni la conversación. Ana
          tampoco te pide ningún dato antes de que aceptes. Solo queda constancia, sin ningún dato tuyo, de que un chat se
          cerró sin aceptar el aviso, con su fecha y su canal, para nuestras estadísticas.
        </p>
      ),
    },
    {
      id: "volver",
      title: "Si vuelves o empiezas de nuevo",
      body: (
        <>
          <p>
            Tu teléfono y tu correo sirven también para encontrar tu solicitud si vuelves. Si das uno que ya tenemos, Ana no
            te cuenta nada de esa solicitud antes de preguntarte si quieres continuar con ella, con el nombre con el que se
            hizo: «Ya tenemos este teléfono registrado. ¿Quieres continuar como Lucía?». Si respondes que sí, retomas esa
            solicitud donde se quedó; si respondes que no, sigues con una solicitud nueva que no hereda nada de la otra.
          </p>
          <p>
            El botón «Empezar de nuevo» abre una solicitud nueva. Si la de ese chat seguía abierta, se cierra y no se te
            vuelve a ofrecer; una ya cerrada se queda como estaba. Las dos se conservan y se borran según los plazos de este
            aviso.
          </p>
        </>
      ),
    },
    {
      id: "destinatarios",
      title: "Quién más accede a tus datos",
      body: (
        <>
          <p>No vendemos tus datos ni los usamos para publicidad. Además del equipo de selección de tu país, intervienen:</p>
          <NoticeTable
            head={["Proveedor", "Qué hace"]}
            rows={[
              [
                "OpenRouter, Inc. (EE. UU.)",
                "Da acceso a los modelos de IA que interpretan tus mensajes y redactan las respuestas de Ana. Lo usamos con retención de datos cero: no guarda los mensajes tras responder y los proveedores de los modelos no pueden usarlos para entrenar ni para otros fines.",
              ],
              [
                "ElevenLabs (EE. UU.), solo en la entrevista por voz",
                "Convierte tu voz en texto y las respuestas de Ana en voz durante la llamada. Nuestra configuración le pide que no guarde la grabación y que borre la transcripción al terminar la llamada; nuestra cuenta acepta esa configuración y estamos verificando el borrado tras una llamada real; hasta entonces ElevenLabs puede conservar una copia según sus propias condiciones. Nosotros guardamos la conversación solo en nuestra base de datos.",
              ],
              [
                "Google (Google Workspace: Calendar y Meet)",
                "Agenda tu entrevista: crea el evento con tu nombre, tu correo y la fecha y hora y te envía la invitación por correo. Por videollamada, en el calendario de la persona del equipo de selección que te entrevista, con el enlace de Google Meet; en persona, en nuestro calendario de entrevistas, con la dirección del local y la persona encargada del local, que es quien te entrevista, como invitada. Si cambias la cita, el evento se mueve; si la anulas, borras tu candidatura o se cumple el plazo de conservación, lo borramos.",
              ],
              [
                "Google Cloud (región de Madrid, UE)",
                "Aloja nuestros servidores y la base de datos de candidaturas de España y de México.",
              ],
              [
                "Monitorización propia",
                "Los registros técnicos y las métricas del servicio se guardan en nuestra propia infraestructura. No usamos herramientas de analítica de terceros ni cookies publicitarias.",
              ],
            ]}
          />
          <p>
            Todos actúan como encargados del tratamiento con un contrato que les obliga a usar los datos solo para prestarnos
            el servicio. En México son remisiones a encargados, que no requieren tu consentimiento.
          </p>
        </>
      ),
    },
    {
      id: "transferencias",
      title: "Transferencias internacionales",
      body: (
        <p>
          Tus datos se alojan en la Unión Europea. Los mensajes que procesa OpenRouter pueden tratarse en Estados Unidos solo
          durante el tiempo necesario para generar la respuesta, y si haces la entrevista por voz, el audio de la llamada lo
          procesa ElevenLabs en Estados Unidos. El evento de tu entrevista en Google Calendar puede tratarse fuera de la
          Unión Europea, en los centros de datos de Google. Esas transferencias se amparan en las cláusulas contractuales
          tipo aprobadas por la Comisión Europea. Si te postulas en México, tus datos se alojan en la Unión Europea, con un
          nivel de protección equivalente al que exige la LFPDPPP.
        </p>
      ),
    },
    {
      id: "conservacion",
      title: "Cuánto tiempo los guardamos",
      body: (
        <ul>
          <li>
            Tu candidatura y la conversación, también si la cerró Ana, si lo dejaste o si empezaste de nuevo: un máximo de
            12 meses desde tu último mensaje. Después se borran.
          </li>
          <li>
            Si aceptas que guardemos tus datos en la bolsa de candidatos para futuras vacantes: también un máximo de 12 meses
            desde tu último mensaje, o hasta que nos pidas que los borremos.
          </li>
          <li>Si no aceptas el aviso al empezar: no guardamos ningún dato tuyo.</li>
          <li>
            La grabación de tu voz en una entrevista por voz: nosotros no la guardamos, y pedimos a ElevenLabs que tampoco
            lo haga (nuestra cuenta acepta esa configuración; el borrado tras la llamada está pendiente de verificar).
          </li>
          <li>
            El evento de tu entrevista en el calendario: se borra junto con tu candidatura (a los 12
            meses o cuando nos lo pidas), y antes si anulas la cita.
          </li>
          <li>Las copias de seguridad: 30 días, así que lo que borramos desaparece también de ellas en un mes.</li>
          <li>
            Si te contratamos, los datos pasan a tu expediente laboral, con su propio aviso de privacidad para empleados.
          </li>
        </ul>
      ),
    },
    {
      id: "derechos",
      title: "Tus derechos",
      body: (
        <>
          <p>
            <strong>En España</strong> puedes ejercer tus derechos de acceso, rectificación, supresión, oposición,
            limitación del tratamiento y portabilidad, y retirar tu consentimiento en cualquier momento sin que afecte a lo
            ya tratado. Te respondemos en un plazo de un mes. Si no estás de acuerdo con nuestra respuesta, puedes reclamar
            ante la <External href="https://www.aepd.es">Agencia Española de Protección de Datos</External>.
          </p>
          <p>
            <strong>En México</strong> puede ejercer sus derechos ARCO (acceso, rectificación, cancelación y oposición),
            revocar su consentimiento y limitar el uso o la divulgación de sus datos. Su solicitud debe incluir su nombre, un
            medio para responderle, una copia de su identificación y la descripción de lo que pide. Le respondemos en un plazo
            máximo de 20 días hábiles. Si no está de acuerdo con la respuesta, puede acudir a la Secretaría Anticorrupción y
            Buen Gobierno, autoridad de protección de datos personales.
          </p>
          <p>
            En los dos países, escribe a <Mail /> e indica el correo o el teléfono que diste en el chat para que
            encontremos tu candidatura.
          </p>
          <p>
            <strong>Para borrar tu candidatura</strong> no tienes que esperar a los 12 meses: en cuanto comprobamos que la
            solicitud es tuya, borramos tus datos, tus conversaciones con Ana y tu cita de entrevista, que queda libre para
            otra persona, también del calendario (si la cita aún no ha pasado, te llega el
            aviso de cancelación). De las copias de seguridad desaparecen en 30 días como máximo.
          </p>
        </>
      ),
    },
    {
      id: "cambios",
      title: "Cambios en este aviso",
      body: (
        <p>
          Si cambiamos este aviso, publicaremos la nueva versión en esta página con su fecha de actualización.
        </p>
      ),
    },
  ],
};
