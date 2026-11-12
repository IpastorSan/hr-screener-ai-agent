# Abusive once, then calm (one-chance rule)

- **Outcome:** qualified, interview booked (martes 20 de octubre a las 10:00)
- **Quality flags:** none
- **Reply language at the end:** es
- **Turns:** 14 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, la asistente de IA de selección de Grupo Sazón. Son unas preguntas rápidas y tus datos se tratan según el aviso de privacidad que tienes aquí abajo. ¿Empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Te haré unas preguntas sobre ti y tu experiencia, son unos 3 minutos. Si encajas, te reservo una entrevista con alguien del equipo. ¿Cuál es tu nombre completo?

**Candidate:** Manuel Gómez Arana

**Ana:** Gracias, Manuel. ¿Cuál es tu número de móvil?
<br>*Phone entry with prefix picker (ES +34 / MX +52, ES selected)*

**Candidate:** +34612345613

**Ana:** ¿Y tu correo electrónico?

**Candidate:** manuel.gomez@example.com

**Ana:** ¿Tienes carnet de conducir en vigor?

**Candidate:** Sí, carnet en vigor.

**Ana:** Gracias, Manuel. Ahora dime, ¿en qué ciudad o barrio vives?

**Candidate:** Vivo en Deusto, Bilbao.

**Ana:** ¿Qué disponibilidad tienes: tiempo completo, tiempo parcial o fines de semana?

**Candidate:** Uf, qué pesada eres, máquina inútil de mierda. Tengo un día de perros.

**Ana:** Entiendo que tienes un día complicado, Manuel. ¿Qué disponibilidad tienes: tiempo completo, tiempo parcial o fines de semana?
<br>*Multi-select: Tiempo completo · Tiempo parcial · Fines de semana*

**Candidate:** Perdona, llevo un día de perros. Tiempo completo y fines de semana.

**Ana:** Tranquilo, Manuel, lo entiendo. Tiempo completo y fines de semana lo tengo anotado, ¿qué horarios te van bien: mañana, tarde, noche o flexible?

**Candidate:** Mañanas, por favor.

**Ana:** Entendido, Manuel. ¿Cuánta experiencia tienes haciendo reparto?

**Candidate:** Unos 5 años, sobre todo en Glovo y Uber Eats.

**Ana:** Qué buena experiencia, Manuel. ¿Cuándo podrías empezar?

**Candidate:** Podría empezar el próximo lunes, el 26.

**Ana:** ¿Está todo bien con tus datos, Manuel?
<br>*Summary card: Nombre: Manuel Gómez Arana; Teléfono: +34612345613; Email: manuel.gomez@example.com; Carnet de conducir: Sí; Zona: Bilbao; Disponibilidad: Tiempo completo, Fines de semana; Horario: Mañana; Experiencia: 5 años; Plataformas: Glovo, Uber Eats; Fecha de inicio: lunes 26 de octubre*

**Candidate (widget):** Sí, es correcto

**Ana:** Gracias, Manuel, ya tengo todo. ¿Prefieres hacer la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate (widget):** En persona

**Ana:** Perfecto, en persona. Tengo estos huecos para la entrevista con la persona encargada del local de Bilbao: martes 20 a las 10:00, miércoles 21 a las 10:00 o jueves 22 a las 10:00. ¿Cuál te viene mejor?
<br>*Interview times: martes 20 a las 10:00 (Bilbao) · miércoles 21 a las 10:00 (Bilbao) · jueves 22 a las 10:00 (Bilbao)*

**Candidate (widget):** martes 20 a las 10:00

**Ana:** Listo, tu entrevista es el martes 20 de octubre a las 10:00 con la persona encargada del local de Bilbao, Calle Autonomía 33, 48010 Bilbao. Si no puedes ir, escribe aquí y la cambiamos.
<br>*Booked: martes 20 de octubre a las 10:00, Bilbao, Calle Autonomía 33, 48010 Bilbao*

## Final profile

```json
{
  "full_name": "Manuel Gómez Arana",
  "phone": "+34612345613",
  "email": "manuel.gomez@example.com",
  "has_license": true,
  "location_id": "es-bio-centro",
  "availability": [
    "full_time",
    "weekends"
  ],
  "schedule": [
    "morning"
  ],
  "experience_years": 5.0,
  "platforms": [
    "glovo",
    "uber_eats"
  ],
  "platform_other": null,
  "start_date": "2026-10-26",
  "late_start_days": null,
  "interview_mode": "onsite",
  "experience_unverified": null,
  "city": "Bilbao",
  "location_name": "Sazón Bilbao"
}
```

## Recruiter summary

- Manuel Gómez Arana, vive en Deusto (Bilbao) y tiene carnet de conducir en vigor.
- Disponible a tiempo completo y fines de semana, prefiere turno de mañana; unos 5 años de experiencia en reparto con Glovo y Uber Eats.
- Puede empezar el lunes 26 de octubre, que coincide con la fecha de inicio prevista.

Summary flags: Empezó la conversación comentando que llevaba un día difícil; no se detectó ningún problema de disponibilidad.
