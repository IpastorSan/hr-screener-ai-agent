# No offered time works, twice, so the application is saved

- **Outcome:** qualified, no interview time free or suited; application saved
- **Quality flags:** none
- **Reply language at the end:** es
- **Turns:** 13 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, la asistente de IA de selección de Grupo Sazón. Son unas preguntas rápidas y tus datos se tratan según el aviso de privacidad que tienes abajo, ¿empezamos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Te haré unas preguntas sobre ti y tu experiencia, son unos 3 minutos. Si encajas, te reservo una entrevista con alguien del equipo. ¿Cuál es tu nombre completo?

**Candidate:** Iván Torres Llorca

**Ana:** Gracias, Iván. ¿Cuál es tu número de móvil?
<br>*Phone entry with prefix picker (ES +34 / MX +52, ES selected)*

**Candidate:** 612345619

**Ana:** ¿Y tu correo electrónico?

**Candidate:** ivan.torres@example.com

**Ana:** ¿Tienes carnet de conducir en vigor?

**Candidate:** Sí, tengo el carnet en vigor desde hace años.

**Ana:** Perfecto, Iván. ¿En qué ciudad o barrio vives?

**Candidate:** Vivo en el centro de Alicante.

**Ana:** Entendido, Iván. ¿Qué disponibilidad tienes: jornada completa, media jornada o solo fines de semana?

**Candidate:** Media jornada, y solo por las noches.

**Ana:** ¿Cuántos años de experiencia tienes haciendo reparto?

**Candidate:** Un año, trabajé con Just Eat.

**Ana:** Iván, un año con Just Eat es buena base. ¿Cuándo podrías empezar?

**Candidate:** Podría empezar el lunes que viene, el 26.

**Ana:** ¿Está todo bien con tus datos?
<br>*Summary card: Nombre: Iván Torres Llorca; Teléfono: +34612345619; Email: ivan.torres@example.com; Carnet de conducir: Sí; Zona: Alicante; Disponibilidad: Tiempo parcial; Horario: Noche; Experiencia: 1 año; Plataformas: Just Eat; Fecha de inicio: lunes 26 de octubre*

**Candidate (widget):** Sí, es correcto

**Ana:** Gracias, Iván, ya tenemos todo lo necesario. ¿Prefieres la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate (widget):** Videollamada

**Ana:** Perfecto, por videollamada. Tengo estos huecos: martes 20 a las 18:00, miércoles 21 a las 18:00 o jueves 22 a las 18:00. ¿Cuál te viene mejor?
<br>*Interview times: martes 20 a las 18:00 (Alicante) · miércoles 21 a las 18:00 (Alicante) · jueves 22 a las 18:00 (Alicante)*

**Candidate:** Uf, ninguna me va bien, trabajo a esas horas en la tienda. ¿Tendrás algún hueco por la noche?

**Ana:** Entiendo, Iván. Estas son las únicas videollamadas que tengo libres ahora mismo: viernes 23 a las 18:00, lunes 26 a las 18:00 o martes 27 a las 18:00. ¿Cuál te viene mejor?
<br>*Interview times: viernes 23 a las 18:00 (Alicante) · lunes 26 a las 18:00 (Alicante) · martes 27 a las 18:00 (Alicante)*

**Candidate:** Tampoco me va ninguna de esas, trabajo a esas horas. ¿Hay algún hueco por las noches?

**Ana:** Entendido, Iván. Ahora mismo no hay más horarios libres en ese local, pero tu solicitud queda guardada.

## Final profile

```json
{
  "full_name": "Iván Torres Llorca",
  "phone": "+34612345619",
  "email": "ivan.torres@example.com",
  "has_license": true,
  "location_id": "es-ali-centro",
  "availability": [
    "part_time"
  ],
  "schedule": [
    "evening"
  ],
  "experience_years": 1.0,
  "platforms": [
    "just_eat"
  ],
  "platform_other": null,
  "start_date": "2026-10-26",
  "late_start_days": null,
  "interview_mode": "video",
  "experience_unverified": null,
  "city": "Alicante",
  "location_name": "Sazón Alicante"
}
```

## Recruiter summary

- Iván Torres Llorca, vive en el centro de Alicante y tiene carnet de conducir en vigor.
- Busca media jornada solo por las noches; tiene 1 año de experiencia repartiendo con Just Eat.
- Podría empezar el lunes 26 de octubre, que coincide con la fecha de inicio indicada.
