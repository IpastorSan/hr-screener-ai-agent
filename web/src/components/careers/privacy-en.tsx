import { External, Mail, NoticeTable, type PrivacyContent } from "./privacy-notice";

export const privacyEn: PrivacyContent = {
  lang: "en",
  title: "Privacy notice for applicants",
  updated: "Last updated: 21 October 2026",
  intro: (
    <p>
      This notice explains what we do with your data when you apply for a delivery driver job at Grupo Sazón through our
      screening chat. It follows the General Data Protection Regulation (GDPR) and Spain&apos;s LOPDGDD, and Mexico&apos;s
      Federal Law on the Protection of Personal Data Held by Private Parties (LFPDPPP).
    </p>
  ),
  summaryTitle: "In short",
  summary: [
    <>
      The chat is run by <strong>Ana, an artificial intelligence assistant</strong>, not a person.
    </>,
    "It only starts if you say yes to this notice. If you say no, the chat ends and we store neither your data nor the conversation.",
    "Ana closes an application on her own in a few cases only: you have no valid driving licence, you live outside our delivery areas, she can't get your name, licence, city or a way to contact you (you can start again), or someone tries to manipulate her. You can stop whenever you want.",
    "Hiring decisions are always made by Grupo Sazón's recruiting team, and you can ask a person to review any decision Ana makes.",
    "We keep your application for at most 12 months, and you can ask us to delete it at any time.",
  ],
  tocTitle: "Contents",
  sections: [
    {
      id: "controller",
      title: "Who is responsible for your data",
      body: (
        <>
          <p>The controller depends on the country of the job you apply for:</p>
          <NoticeTable
            head={["Country", "Controller"]}
            rows={[
              ["Spain", "Grupo Sazón Restauración, S.L., CIF B-00000000, calle del Pimentón 14, 28012 Madrid."],
              [
                "Mexico",
                "Grupo Sazón México, S.A. de C.V., RFC GSM000000AA0, avenida de los Insurgentes Sur 000, colonia Roma Norte, alcaldía Cuauhtémoc, 06700 Mexico City.",
              ],
            ]}
          />
          <p>
            For any privacy question, in both countries: <Mail />. Our data protection officer can also be reached at that
            address.
          </p>
        </>
      ),
    },
    {
      id: "data",
      title: "What data we process",
      body: (
        <>
          <ul>
            <li>
              <strong>Identity and contact:</strong> full name, mobile number and email address. The phone number or the email is enough; without an email we can&apos;t send you the interview invitation, which you will then see only in the chat.
            </li>
            <li>
              <strong>Screening answers:</strong> whether you hold a driving licence, your city or area and the matching
              kitchen, your availability, preferred shift, delivery experience and the platforms you have worked with, and
              when you could start.
            </li>
            <li>
              <strong>The conversation:</strong> the messages you exchange with Ana and, if you book one, your interview
              appointment.
            </li>
            <li>
              <strong>If you do the interview by voice:</strong> your voice, which is turned into text during the call. We
              keep the transcript like any other conversation, not the recording.
            </li>
            <li>
              <strong>Technical data:</strong> the country and kitchen of the job posting you came from, and your
              browser&apos;s language and time zone, which we use to pick the language and show interview times in your local
              time.
            </li>
          </ul>
          <p>
            We do not ask for sensitive data such as health, religion or ethnic origin. If you write it in the chat, we do
            not use it for screening.
          </p>
        </>
      ),
    },
    {
      id: "purposes",
      title: "Why we use it, and on what basis",
      body: (
        <>
          <NoticeTable
            head={["Purpose", "Legal basis"]}
            rows={[
              [
                "Handling your application: the screening conversation, the interview and contacting you about it.",
                "Spain: your consent, given with the \"Yes\" button at the start (GDPR art. 6(1)(a)), and steps taken at your request before a contract (art. 6(1)(b)). Mexico: primary purpose, with your express consent.",
              ],
              [
                "Keeping your data in our talent pool for future vacancies, only if you agree to it in the chat.",
                "Spain: your consent (GDPR art. 6(1)(a)). Mexico: secondary purpose.",
              ],
              [
                "Improving Ana by reviewing conversations where she misunderstood an answer, and producing aggregate statistics about the process.",
                "Spain: our legitimate interest in a process that works well (GDPR art. 6(1)(f)). Mexico: secondary purpose.",
              ],
            ]}
          />
          <p>
            In Mexico you can refuse the secondary purposes at any time by writing to <Mail />; refusing does not affect your
            application.
          </p>
        </>
      ),
    },
    {
      id: "ai",
      title: "AI assistant and automated decisions",
      body: (
        <>
          <p>
            Ana is an artificial intelligence system and says so in her first message, as Article 50 of the EU Artificial
            Intelligence Act requires. We apply the same standard in Mexico. Ana asks the screening questions, answers common
            questions about the job and offers you interview times.
          </p>
          <p>
            That regulation classes AI systems used to select staff as <strong>high-risk</strong>, and this is one of them:
            we inform you that a high-risk AI system is used in this screening (Article 26(11)).
          </p>
          <p>
            Ana closes an application <strong>automatically</strong> in these cases only:
          </p>
          <ul>
            <li>
              <strong>You do not hold a driving licence that is valid</strong> when you apply (one you are still getting, a
              learner&apos;s permit, or an expired, suspended or being-renewed licence does not count). You can apply again
              once you have one.
            </li>
            <li>
              <strong>You live outside every one of our delivery areas</strong>: before closing, Ana asks whether that is
              where you live now. She may offer to keep your data in the talent pool, only if you agree.
            </li>
            <li>
              <strong>Essential data is missing</strong>: after several tries she could not confirm your first name and
              surname, whether you hold a licence, your city, or at least a phone number or an email to contact you. She
              tells you what was missing, and you can start again whenever you like with the &quot;Start again&quot;
              button.
            </li>
            <li>
              <strong>An attempt to manipulate her</strong>: instructions aimed at the system instead of answers, hidden in
              invisible characters or encoded (for example in Morse code or runes). She only tells you she can&apos;t
              continue with the application. You may apply again, with a warning that another attempt will close it again;
              after three closes for this reason with the same phone number or email, no new application can be opened with
              those details.
            </li>
          </ul>
          <p>
            In every case she tells you why in the same chat. Your availability, shift, experience or start date never
            close an application: if you would rather not answer one of those questions, it is left blank. A second
            insulting message ends the conversation.
          </p>
          <p>
            You can also <strong>stop yourself</strong> at any time: if you write that you do not want to go on, Ana says
            goodbye and the application is closed. What you already answered is kept like any other application (see
            &quot;How long we keep it&quot;), and you can ask us to delete it.
          </p>
          <p>
            Ana does not decide who is hired. She prepares a summary of your answers for Grupo Sazón&apos;s recruiting team,
            who make every hiring decision after the interview.
          </p>
          <p>
            If Ana closes your application for any of these reasons, you have the right to <strong>obtain human intervention</strong> from the
            recruiting team, to <strong>express your point of view</strong> and to <strong>contest the decision</strong>{" "}
            (GDPR Article 22(3)). Write to <Mail />: a person reviews your case and can take your application forward even
            if Ana closed it, including for an attempt to manipulate her: the recruiting team can override any outcome from
            Ana. You can also ask us how this automated processing works, or object to it.
          </p>
        </>
      ),
    },
    {
      id: "declining",
      title: "If you do not accept this notice",
      body: (
        <p>
          If you say no at the start, the chat ends and we do not store your name, your answers or the conversation. Ana
          does not ask for any data before you accept. All that remains, with no data about you, is a record that a chat
          closed without accepting the notice, with its date and channel, for our statistics.
        </p>
      ),
    },
    {
      id: "returning",
      title: "If you come back or start again",
      body: (
        <>
          <p>
            Your phone number and email are also used to find your application if you come back. If you give one we
            already have, Ana tells you nothing about that application before asking whether you want to carry on with it,
            under the name it was made with: &quot;We already have this phone number on file. Do you want to continue
            as Lucía?&quot;. If you say yes, you pick that application up where it stopped; if you say no, you go on with a new
            application that inherits nothing from the other one.
          </p>
          <p>
            The &quot;Start again&quot; button opens a new application. If the one in that chat was still open, it is
            closed and not offered to you again; one already closed stays as it was. Both are kept and deleted under the
            periods in this notice.
          </p>
        </>
      ),
    },
    {
      id: "recipients",
      title: "Who else has access to your data",
      body: (
        <>
          <p>
            We do not sell your data or use it for advertising. Besides the recruiting team in your country, these providers
            are involved:
          </p>
          <NoticeTable
            head={["Provider", "What it does"]}
            rows={[
              [
                "OpenRouter, Inc. (USA)",
                "Gives access to the AI models that read your messages and write Ana's replies. We use it with zero data retention: it does not keep messages after replying, and model providers may not use them for training or any other purpose.",
              ],
              [
                "ElevenLabs (USA), only for the voice interview",
                "Turns your voice into text and Ana's replies into speech during the call. Our configuration asks it to keep no recording and to delete the transcript when the call ends; our account accepts these settings and we are verifying deletion after a real call; until then ElevenLabs may keep a copy under its own terms. We store the conversation only in our own database.",
              ],
              [
                "Google (Google Workspace: Calendar and Meet)",
                "Schedules your interview: creates the event with your name, your email and the date and time, and emails you the invitation. For a video interview, in the calendar of the recruiting team member who interviews you, with a Google Meet link; in person, in our interview calendar, with the kitchen's address and the kitchen's manager, who interviews you, invited. If you move the interview the event moves; if you cancel it, delete your application or the retention period ends, we delete it.",
              ],
              ["Google Cloud (Madrid region, EU)", "Hosts our servers and the application database for Spain and Mexico."],
              [
                "Our own monitoring",
                "Technical logs and service metrics are kept on our own infrastructure. We use no third-party analytics tools and no advertising cookies.",
              ],
            ]}
          />
          <p>
            All of them act as processors under a contract that limits them to providing the service to us. Under Mexican
            law these are transfers to processors (remisiones), which do not require your consent.
          </p>
        </>
      ),
    },
    {
      id: "transfers",
      title: "International transfers",
      body: (
        <p>
          Your data is hosted in the European Union. Messages handled by OpenRouter may be processed in the United States only
          for as long as it takes to generate a reply, and if you do the interview by voice, ElevenLabs processes the call
          audio in the United States. The event for your interview in Google Calendar may be processed outside the European
          Union, in data centres run by Google. Those transfers rely on the standard contractual clauses approved by
          the European Commission. If you apply in Mexico, your data is hosted in the European Union, with a level of
          protection equivalent to what the LFPDPPP requires.
        </p>
      ),
    },
    {
      id: "retention",
      title: "How long we keep it",
      body: (
        <ul>
          <li>
            Your application and conversation, also when Ana closed it, you stopped or you started again: at most 12 months
            from your last message. Then they are deleted.
          </li>
          <li>
            If you agree to us keeping your data in the talent pool for future vacancies: also at most 12 months from your last
            message, or until you ask us to delete it.
          </li>
          <li>If you do not accept this notice at the start: we store no data about you.</li>
          <li>
            The recording of your voice in a voice interview: we do not keep it, and we ask ElevenLabs not to either (our account accepts this setting;
            deletion after the call is still being verified).
          </li>
          <li>
            The event for your interview in the calendar: deleted with your application (after 12 months or
            when you ask us), and earlier if you cancel the interview.
          </li>
          <li>Backups: 30 days, so whatever we delete is also gone from them within a month.</li>
          <li>If we hire you, the data moves to your employee file, which has its own privacy notice.</li>
        </ul>
      ),
    },
    {
      id: "rights",
      title: "Your rights",
      body: (
        <>
          <p>
            <strong>In Spain</strong> you can exercise your rights of access, rectification, erasure, objection, restriction
            of processing and portability, and withdraw your consent at any time without affecting earlier processing. We
            reply within one month. If you disagree with our reply, you can complain to the{" "}
            <External href="https://www.aepd.es">Spanish Data Protection Agency (AEPD)</External>.
          </p>
          <p>
            <strong>In Mexico</strong> you can exercise your ARCO rights (access, rectification, cancellation and
            objection), revoke your consent and limit the use or disclosure of your data. Your request must include your
            name, a way to reply to you, a copy of your ID and a description of what you are asking for. We reply within 20
            business days. If you disagree with the reply, you can turn to the Secretaría Anticorrupción y Buen Gobierno, the
            personal data protection authority.
          </p>
          <p>
            In both countries, write to <Mail /> and include the email or phone number you gave in the chat so we can find
            your application.
          </p>
          <p>
            <strong>To delete your application</strong> you do not have to wait 12 months: once we have checked the request
            is yours, we delete your data, your conversations with Ana and your interview booking, which is freed for someone
            else, including the calendar event (if the interview is still ahead, you get the
            cancellation notice). Backups lose it within 30 days at most.
          </p>
        </>
      ),
    },
    {
      id: "changes",
      title: "Changes to this notice",
      body: <p>If we change this notice, we will publish the new version on this page with its update date.</p>,
    },
  ],
};
