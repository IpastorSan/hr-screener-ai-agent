# Returning candidate, resumes the open application

- **Outcome:** qualified, interview booked (martes 20 de octubre a las 10:00)
- **Quality flags:** none
- **Reply language at the end:** es
- **Turns:** 13 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, la asistente de IA de selección de Grupo Sazón. Tus datos se tratan según el aviso de privacidad que tienes aquí abajo. ¿Empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Te haré unas preguntas sobre ti y tu experiencia, son unos 3 minutos. Si encajas, te reservo una entrevista con alguien del equipo. ¿Cuál es tu nombre completo?

**Candidate:** Elena Vidal Puig

**Ana:** ¿Cuál es tu número de móvil?
<br>*Phone entry with prefix picker (ES +34 / MX +52, ES selected)*

**Candidate:** 612345618

**Ana:** Ya tenemos este teléfono registrado. ¿Quieres continuar como Elena?
<br>*Buttons: Sí · No*

**Candidate (widget):** Sí

**Ana:** Hola de nuevo, Elena, seguimos donde lo dejaste. ¿Qué disponibilidad tienes: tiempo completo, tiempo parcial o fines de semana?

**Candidate:** Solo fines de semana, por favor.

**Ana:** Perfecto, Elena, lo anoto como fines de semana. ¿Te van bien solo sábados y domingos, o también algún día entre semana?

**Candidate:** Solo sábados y domingos, entre semana no puedo.

**Ana:** ¿Cuánta experiencia tienes haciendo reparto?

**Candidate:** Un año haciendo reparto en Glovo.

**Ana:** Un año con Glovo es buena base, Elena. ¿Cuándo podrías empezar?

**Candidate:** Podría empezar el lunes que viene, el 26 de octubre.

**Ana:** ¿Está todo correcto?
<br>*Summary card: Nombre: Elena Vidal Puig; Teléfono: +34612345618; Email: elena.vidal@example.com; Carnet de conducir: Sí; Zona: Barcelona Eixample; Disponibilidad: Fines de semana; Horario: Mañana, Tarde, Noche; Experiencia: 1 año; Plataformas: Glovo; Fecha de inicio: lunes 26 de octubre*

**Candidate (widget):** Corregir algo

**Ana:** Vale. ¿Qué dato quieres cambiar?
<br>*Summary card: Nombre: Elena Vidal Puig; Teléfono: +34612345618; Email: elena.vidal@example.com; Carnet de conducir: Sí; Zona: Barcelona Eixample; Disponibilidad: Fines de semana; Horario: Mañana, Tarde, Noche; Experiencia: 1 año; Plataformas: Glovo; Fecha de inicio: lunes 26 de octubre*

**Candidate:** El horario. Solo por la mañana, no tarde ni noche.

**Ana:** ¿Están bien estos datos, Elena?
<br>*Summary card: Nombre: Elena Vidal Puig; Teléfono: +34612345618; Email: elena.vidal@example.com; Carnet de conducir: Sí; Zona: Barcelona Eixample; Disponibilidad: Fines de semana; Horario: Mañana; Experiencia: 1 año; Plataformas: Glovo; Fecha de inicio: lunes 26 de octubre*

**Candidate (widget):** Sí, es correcto

**Ana:** Gracias, ya tengo todo. ¿Prefieres hacer la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate (widget):** En persona

**Ana:** Perfecto, en persona. Tengo estos huecos para la entrevista con la persona encargada del local de Barcelona Eixample: martes 20 a las 10:00, miércoles 21 a las 10:00 o jueves 22 a las 10:00. ¿Cuál te viene mejor?
<br>*Interview times: martes 20 a las 10:00 (Barcelona Eixample) · miércoles 21 a las 10:00 (Barcelona Eixample) · jueves 22 a las 10:00 (Barcelona Eixample)*

**Candidate (widget):** martes 20 a las 10:00

**Ana:** Listo, tu entrevista es el martes 20 de octubre a las 10:00 con la persona encargada del local de Barcelona Eixample, en Carrer del Consell de Cent 287, 08007 Barcelona. Si no puedes asistir, escribe aquí y la cambiamos.
<br>*Booked: martes 20 de octubre a las 10:00, Barcelona Eixample, Carrer del Consell de Cent 287, 08007 Barcelona*

## Final profile

```json
{
  "full_name": "Elena Vidal Puig",
  "phone": "+34612345618",
  "email": "elena.vidal@example.com",
  "has_license": true,
  "location_id": "es-bcn-eixample",
  "availability": [
    "weekends"
  ],
  "schedule": [
    "morning"
  ],
  "experience_years": 1.0,
  "platforms": [
    "glovo"
  ],
  "platform_other": null,
  "start_date": "2026-10-26",
  "late_start_days": null,
  "interview_mode": "onsite",
  "experience_unverified": null,
  "city": "Barcelona",
  "location_name": "Sazón Barcelona Eixample"
}
```

## Recruiter summary

- Elena Vidal Puig, de Barcelona (Eixample), con carnet de conducir y un año de experiencia haciendo reparto con Glovo.
- Disponible solo fines de semana (sábado y domingo), solo por la mañana; puede empezar el lunes 26 de octubre.
- Sin entrevista reservada en este resumen; revisar en la entrevista la disponibilidad limitada a fines de semana y mañanas.

Summary flags: Disponibilidad limitada: solo fines de semana y solo turno de mañana; no puede entre semana ni por la tarde o noche.
