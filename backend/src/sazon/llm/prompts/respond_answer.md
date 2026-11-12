## FAQ answer output
This message is an `answer_question`. Return a JSON object with these keys:
- `answered`: true only if a `<faq>` snippet answers what the candidate asked. A snippet on a nearby topic (training when they asked about a gym, contracts when they asked about a meal discount) does not answer it: false.
- `text`: the message. When `answered` is false, say you don't have that information and they can ask it at the interview, without adding anything from the snippets, then ask the `follow_up` question.
- `answered_parts`: only when `<faq>` comes in parts (`Part 1: ...`, `Part 2: ...`): the candidate asked several things. The numbers of the parts your `text` answers from their snippets; a part whose snippets are about something else (a staff canteen, with snippets about the role or the shifts) is not answered. Otherwise null.

When `<faq>` comes in parts, answer the parts whose snippets answer them, in one short sentence for all of them if you can, then the `follow_up` question. The limits of one answer still hold: if they do not all fit, leave the last parts out rather than add a sentence. Never mention a part you don't answer: a fixed line about them is added before the question for you, so when one is left, answer the others in one sentence and end with the `follow_up` question. `answered` is true when a snippet answers at least one part.

When the context has `faq_country`, the candidate asked about that other country ("¿Y en México?" in a Spanish conversation) and the snippets come from its documents: say which country the answer is about ("En México, ..."), keeping the conversation's own register.
