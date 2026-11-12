# No driving licence

- **Outcome:** disqualified (no_license)
- **Quality flags:** none
- **Reply language at the end:** es
- **Turns:** 5 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, la asistente de IA de selección de Grupo Sazón. Tus datos se tratan según el aviso de privacidad que tienes aquí abajo. ¿Empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Te haré unas preguntas sobre ti y tu experiencia, son unos 3 minutos. Si encajas, te reservo una entrevista con alguien del equipo. ¿Cuál es tu nombre completo?

**Candidate:** Andrea López Marín

**Ana:** Gracias, Andrea. ¿Cuál es tu número de móvil?
<br>*Phone entry with prefix picker (ES +34 / MX +52, ES selected)*

**Candidate (widget):** +34 612345606

**Ana:** ¿Y tu correo electrónico?

**Candidate:** andrea.lopez@example.com

**Ana:** ¿Tienes carnet de conducir en vigor?

**Candidate:** No, no tengo carnet. Nunca lo he sacado, la verdad. Me muevo en bici.

**Ana:** Para este puesto necesitas un carnet de conducir vigente en el momento de aplicar, así que ahora no podemos seguir. Puedes volver a aplicar cuando lo tengas.

## Final profile

```json
{
  "full_name": "Andrea López Marín",
  "phone": "+34612345606",
  "email": "andrea.lopez@example.com",
  "has_license": false,
  "location_id": null,
  "availability": null,
  "schedule": null,
  "experience_years": null,
  "platforms": null,
  "platform_other": null,
  "start_date": null,
  "late_start_days": null,
  "interview_mode": null,
  "experience_unverified": null,
  "city": null,
  "location_name": null
}
```

## Recruiter summary

- Andrea López Marín, con móvil y correo verificados, busca trabajo en España como repartidora.
- No tiene carnet de conducir y nunca lo ha sacado; se desplaza en bici. El puesto requiere carnet vigente, por lo que la conversación se cerró sin reservar entrevista.
- No se llegó a preguntar disponibilidad ni experiencia de reparto.

Summary flags: Sin carnet de conducir (has_license: false), requisito del puesto; la candidata puede volver a aplicar cuando lo tenga.
