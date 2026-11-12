# Goes quiet, gets a nudge, carries on

- **Outcome:** qualified, interview booked (miércoles 7 de octubre a las 18:00)
- **Quality flags:** none
- **Channel:** web
- **Turns:** 13 candidate messages; a scripted candidate from the offline demo data (`sazon seed`), with the keyword extractor and template replies

## Transcript

**Ana:** ¡Hola! Soy Ana, la asistente de IA de selección de Grupo Sazón. Son unas preguntas rápidas y tus datos se tratan según el aviso de privacidad que tienes aquí abajo, ¿empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate:** Sí

**Ana:** Te haré unas preguntas sobre ti y tu experiencia, son unos 3 minutos. Si encajas, te reservo una entrevista con alguien del equipo. ¿Cuál es tu nombre completo?

**Candidate:** Me llamo Javier Serrano Blanco

**Ana:** ¿Cuál es tu número de móvil?
<br>*Phone entry with prefix picker (ES +34 / MX +52, ES selected)*

**Candidate:** 612 345 691

**Ana:** ¿Y tu correo electrónico?

**Candidate:** javier.serrano@example.com

**Ana:** ¿Tienes carnet de conducir en vigor?

**Ana (nudge, after 3 minutes of silence):** Hola de nuevo, tu solicitud está a medias. ¿Tienes carnet de conducir en vigor?

**Candidate:** Sí

**Ana:** ¿En qué ciudad o barrio vives?

**Candidate:** Vivo en Centro, Málaga

**Ana:** ¿Qué disponibilidad tienes: tiempo completo, tiempo parcial o fines de semana?

**Candidate:** Tiempo completo, Fines de semana

**Ana:** ¿Qué horarios te van bien: mañana, tarde, noche o flexible?

**Candidate:** Noche

**Ana:** ¿Cuánta experiencia tienes haciendo reparto?

**Candidate:** 1 años en Uber Eats

**Ana:** ¿Cuándo podrías empezar?

**Candidate:** El 20/12 me viene bien

**Ana:** Estos son tus datos. ¿Está todo bien?
<br>*Summary card: Nombre: Javier Serrano Blanco; Teléfono: +34612345691; Email: javier.serrano@example.com; Carnet de conducir: Sí; Zona: Málaga; Disponibilidad: Tiempo completo, Fines de semana; Horario: Noche; Experiencia: 1 año; Plataformas: Uber Eats; Fecha de inicio: domingo 20 de diciembre*

**Candidate:** Correcto, gracias

**Ana:** Gracias, ya tengo todo. ¿Prefieres hacer la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate:** En persona

**Ana:** Perfecto, en persona. Tengo estos huecos para la entrevista en el local de Málaga: miércoles 7 a las 18:00, jueves 8 a las 18:00 o viernes 9 a las 18:00. ¿Cuál te viene mejor?
<br>*Interview times: miércoles 7 a las 18:00 (Málaga) · jueves 8 a las 18:00 (Málaga) · viernes 9 a las 18:00 (Málaga)*

**Candidate:** miércoles 7 a las 18:00

**Ana:** Listo, tu entrevista es el miércoles 7 de octubre a las 18:00 en el local de Málaga, Calle Carretería 49, 29008 Málaga. Si no puedes ir, escribe aquí y la cambiamos.
<br>*Booked: miércoles 7 de octubre a las 18:00, Málaga, Calle Carretería 49, 29008 Málaga*

## Final profile

```json
{
  "full_name": "Javier Serrano Blanco",
  "phone": "+34612345691",
  "email": "javier.serrano@example.com",
  "has_license": true,
  "location_id": "es-mlg-centro",
  "availability": [
    "full_time",
    "weekends"
  ],
  "schedule": [
    "evening"
  ],
  "experience_years": 1.0,
  "platforms": [
    "uber_eats"
  ],
  "platform_other": null,
  "start_date": "2026-12-20",
  "late_start_days": 75,
  "interview_mode": "onsite",
  "experience_unverified": null,
  "city": "Málaga",
  "location_name": "Sazón Málaga"
}
```

## Recruiter summary

- Nombre: Javier Serrano Blanco; Teléfono: +34612345691; Email: javier.serrano@example.com; Carnet de conducir: Sí; Zona: Málaga; Disponibilidad: Tiempo completo, Fines de semana; Horario: Noche; Experiencia: 1 año; Plataformas: Uber Eats; Fecha de inicio: domingo 20 de diciembre

Summary flags: late_start: 75 days
