# Chooses a video interview, then confirms it

- **Outcome:** qualified, interview booked (martes 20 de octubre a las 14:00)
- **Quality flags:** none
- **Reply language at the end:** es
- **Turns:** 13 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, asistente de IA de reclutamiento de Grupo Sazón. Son unas preguntas rápidas y sus datos se tratan según el aviso de privacidad que tiene aquí abajo. ¿Empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Le haré unas preguntas sobre usted y su experiencia, son unos 3 minutos. Si encaja, le reservo una entrevista con alguien del equipo. ¿Me comparte su nombre completo, por favor?

**Candidate:** Claro, con gusto. Me llamo Daniela Torres Quintero.

**Ana:** Gracias por su nombre, Daniela. ¿Cuál es su número de celular?
<br>*Phone entry with prefix picker (ES +34 / MX +52, MX selected)*

**Candidate:** Mi celular es +52 81 1234 5621

**Ana:** ¿Cuál es su correo electrónico?

**Candidate:** daniela.torres@example.com

**Ana:** ¿Tiene licencia de manejo vigente?

**Candidate:** Sí, tengo licencia vigente.

**Ana:** Perfecto, gracias por confirmar que tiene licencia vigente. ¿En qué ciudad o colonia vive?

**Candidate:** Vivo en San Pedro Garza García, Nuevo León.

**Ana:** ¿Qué disponibilidad tiene: tiempo completo, medio tiempo o fines de semana?

**Candidate:** Medio tiempo y fines de semana, por las tardes. Por las mañanas estudio.

**Ana:** Entiendo, Daniela, medio tiempo por las tardes encaja bien. ¿Cuántos años de experiencia tiene haciendo entregas?

**Candidate:** Tengo como un año trabajando en Rappi.

**Ana:** Entiendo, Daniela, un año con Rappi es buena experiencia. ¿Cuándo podría empezar?

**Candidate:** Podría empezar el 2 de noviembre, si le parece bien.

**Ana:** ¿Está todo correcto con sus datos?
<br>*Summary card: Nombre: Daniela Torres Quintero; Teléfono: +528112345621; Email: daniela.torres@example.com; Licencia de conducir: Sí; Zona: San Pedro Garza García; Disponibilidad: Tiempo parcial, Fines de semana; Horario: Tarde; Experiencia: 1 año; Plataformas: Rappi; Fecha de inicio: lunes 2 de noviembre*

**Candidate (widget):** Sí, es correcto

**Ana:** Gracias, Daniela, ya tengo todo lo necesario. ¿Prefiere hacer la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate (widget):** En persona

**Ana:** Perfecto, en persona. Tengo estos horarios para la entrevista con la gerencia del local de San Pedro Garza García: martes 20 a las 16:00, miércoles 21 a las 16:00 o jueves 22 a las 16:00. ¿Cuál le acomoda mejor?
<br>*Interview times: martes 20 a las 16:00 (San Pedro Garza García) · miércoles 21 a las 16:00 (San Pedro Garza García) · jueves 22 a las 16:00 (San Pedro Garza García)*

**Candidate:** Mejor por videollamada, me queda más cómodo.

**Ana:** Perfecto, Daniela, la entrevista será por videollamada. ¿Cuál de estos horarios le acomoda mejor: martes 20 a las 14:00, miércoles 21 a las 14:00 o jueves 22 a las 14:00?
<br>*Interview times: martes 20 a las 14:00 (San Pedro Garza García) · miércoles 21 a las 14:00 (San Pedro Garza García) · jueves 22 a las 14:00 (San Pedro Garza García)*

**Candidate (widget):** martes 20 a las 14:00

**Ana:** Listo, su entrevista por videollamada es el martes 20 de octubre a las 14:00. Le llegará una invitación con el enlace de Google Meet a su correo, y también lo tiene aquí abajo. Si no puede asistir, escríbanos aquí y la cambiamos.
<br>*Booked: martes 20 de octubre a las 14:00, San Pedro Garza García, Calz. del Valle 409, Col. Del Valle, 66220 San Pedro Garza García*

## Final profile

```json
{
  "full_name": "Daniela Torres Quintero",
  "phone": "+528112345621",
  "email": "daniela.torres@example.com",
  "has_license": true,
  "location_id": "mx-mty-sanpedro",
  "availability": [
    "part_time",
    "weekends"
  ],
  "schedule": [
    "afternoon"
  ],
  "experience_years": 1.0,
  "platforms": [
    "rappi"
  ],
  "platform_other": null,
  "start_date": "2026-11-02",
  "late_start_days": null,
  "interview_mode": "video",
  "experience_unverified": null,
  "city": "San Pedro Garza García",
  "location_name": "Sazón San Pedro Garza García"
}
```

## Recruiter summary

- Daniela Torres Quintero, de San Pedro Garza García, Nuevo León, con licencia de conducir vigente.
- Tiene un año de experiencia haciendo entregas con Rappi; disponible medio tiempo por las tardes y fines de semana, desde el 2 de noviembre de 2026.
- Por las mañanas estudia, por eso su disponibilidad es solo por la tarde y fines de semana.
