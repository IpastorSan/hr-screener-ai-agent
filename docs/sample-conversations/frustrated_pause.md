# Frustrated, accepts a pause

- **Outcome:** paused
- **Quality flags:** frustration
- **Reply language at the end:** es
- **Turns:** 9 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, asistente de IA de reclutamiento de Grupo Sazón. Son unas preguntas rápidas y sus datos se tratan según el aviso de privacidad que tiene aquí abajo. ¿Empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Le haré unas preguntas sobre usted y su experiencia, son unos 3 minutos. Si encaja, le reservo una entrevista con alguien del equipo. ¿Me comparte su nombre completo, por favor?

**Candidate:** Rosa Jiménez Cruz

**Ana:** Gracias, Rosa. ¿Cuál es su número de celular?
<br>*Phone entry with prefix picker (ES +34 / MX +52, MX selected)*

**Candidate:** 2221234514

**Ana:** ¿Y cuál es su correo electrónico?

**Candidate:** rosa.jimenez@example.com

**Ana:** ¿Tiene licencia de manejo vigente?

**Candidate:** Sí, tengo licencia vigente.

**Ana:** ¿En qué ciudad o colonia vive, Rosa?

**Candidate:** Centro Histórico, Puebla.

**Ana:** Gracias, Rosa, ya tengo su zona. ¿Qué disponibilidad tiene: tiempo completo, medio tiempo o fines de semana?

**Candidate:** ¿otra pregunta? esto es eterno

**Ana:** Entiendo, Rosa. ¿Qué disponibilidad tiene: tiempo completo, medio tiempo o fines de semana?
<br>*Multi-select: Tiempo completo · Tiempo parcial · Fines de semana*

**Candidate:** ya me cansé, son demasiadas preguntas

**Ana:** Entiendo, Rosa. ¿Prefiere que lo dejemos aquí y sigamos más tarde donde nos quedamos?
<br>*Buttons: Sí · No*

**Candidate (widget):** Sí

**Ana:** Perfecto, lo dejamos aquí. Vuelva a este chat cuando guste y seguimos desde donde se quedó.

## Final profile

```json
{
  "full_name": "Rosa Jiménez Cruz",
  "phone": "+522221234514",
  "email": "rosa.jimenez@example.com",
  "has_license": true,
  "location_id": "mx-pue-centro",
  "availability": null,
  "schedule": null,
  "experience_years": null,
  "platforms": null,
  "platform_other": null,
  "start_date": null,
  "late_start_days": null,
  "interview_mode": null,
  "experience_unverified": null,
  "city": "Puebla",
  "location_name": "Sazón Puebla"
}
```

## Recruiter summary

(none)
