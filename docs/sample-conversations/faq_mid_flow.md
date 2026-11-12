# Asks FAQ questions mid-flow

- **Outcome:** qualified, interview booked (martes 20 de octubre a las 16:00)
- **Quality flags:** unknown_faq
- **Reply language at the end:** es
- **Turns:** 12 candidate messages; models google/gemini-3.5-flash-lite@minimal / anthropic/claude-haiku-5.5
- **Checks:** all passed

## Transcript

**Ana:** ¡Hola! Soy Ana, asistente de IA de reclutamiento de Grupo Sazón. Sus datos se tratan según el aviso de privacidad que aparece abajo. ¿Está de acuerdo en que empecemos?
<br>*Buttons: Sí · No (link: Leer el aviso de privacidad)*

**Candidate (widget):** Sí

**Ana:** Le haré unas preguntas sobre usted y su experiencia, son unos 3 minutos. Si encaja, le reservo una entrevista con alguien del equipo. ¿Me comparte su nombre completo, por favor?

**Candidate:** Hola, soy Carmen Ortiz Luna. ¿Y cuánto paga el puesto, más o menos?

**Ana:** En México el sueldo va de 2,100 a 2,600 pesos (MXN) a la semana por jornada completa, según la ciudad, y se paga cada semana por depósito. Las propinas van aparte. ¿Me comparte su número de celular?
<br>*Phone entry with prefix picker (ES +34 / MX +52, MX selected)*

**Candidate:** Mi celular es 8112345611. ¿Y el puesto es de tiempo completo?

**Ana:** No tengo esa información; puede preguntarlo en la entrevista. ¿Y su correo electrónico?

**Candidate:** Es carmen.ortiz@example.com. Oiga, ¿y ocupo tener licencia para el puesto?

**Ana:** Sí, se necesita una licencia vigente que permita manejar motocicleta en su estado, por ejemplo la tipo A en Ciudad de México. ¿Tiene licencia de conducir vigente?

**Candidate:** Sí, tengo licencia vigente. ¿Qué documentos ocupo llevar?

**Ana:** Para la entrevista basta con su INE, su CURP y su licencia de conducir. ¿En qué ciudad o colonia vive?

**Candidate:** Vivo en Mitras, en Monterrey.

**Ana:** Gracias por compartirlo, Carmen. ¿Qué disponibilidad tiene: tiempo completo, medio tiempo o fines de semana?

**Candidate:** Tiempo completo, sí.

**Ana:** Entendido, Carmen. ¿Qué horario le acomoda: mañana, tarde, noche o flexible?

**Candidate:** Por la tarde, por favor. Oiga, si tengo un accidente manejando, ¿el seguro me cubre?

**Ana:** Sí, en México el alta en el IMSS es desde el primer día y cubre los riesgos de trabajo, y las motos de la empresa tienen seguro de cobertura amplia. Si usa moto propia, debe llevar al menos seguro de responsabilidad civil. ¿Cuántos años de experiencia tiene haciendo entregas?

**Candidate:** Tengo 2 años haciendo entregas en Rappi. ¿Y el puesto empieza pronto? Yo podría arrancar el 2 de noviembre.

**Ana:** Desde la entrevista hasta el primer turno suelen pasar entre una y dos semanas, según los documentos y las vacantes de la sucursal. Estos son sus datos, ¿está todo correcto?
<br>*Summary card: Nombre: Carmen Ortiz Luna; Teléfono: +528112345611; Email: carmen.ortiz@example.com; Licencia de conducir: Sí; Zona: Monterrey; Disponibilidad: Tiempo completo; Horario: Tarde; Experiencia: 2 años; Plataformas: Rappi; Fecha de inicio: lunes 2 de noviembre*

**Candidate:** Ok, va. Oiga, ¿la sucursal tiene gimnasio para los empleados?

**Ana:** Gracias, Carmen, ya tengo todo lo que necesito. ¿Prefiere hacer la entrevista en persona o por videollamada?
<br>*Buttons: En persona · Videollamada*

**Candidate (widget):** En persona

**Ana:** Perfecto, en persona. Tengo estos horarios para la entrevista con la gerencia del local de Monterrey: martes 20 a las 16:00, miércoles 21 a las 16:00 o jueves 22 a las 16:00. ¿Cuál le acomoda mejor?
<br>*Interview times: martes 20 a las 16:00 (Monterrey) · miércoles 21 a las 16:00 (Monterrey) · jueves 22 a las 16:00 (Monterrey)*

**Candidate (widget):** martes 20 a las 16:00

**Ana:** Listo, su entrevista es el martes 20 de octubre a las 16:00 con la gerencia del local de Monterrey, Av. Benito Juárez 905, Col. Centro, 64000 Monterrey. Si no puede asistir, escriba aquí y la cambiamos.
<br>*Booked: martes 20 de octubre a las 16:00, Monterrey, Av. Benito Juárez 905, Col. Centro, 64000 Monterrey*

## Final profile

```json
{
  "full_name": "Carmen Ortiz Luna",
  "phone": "+528112345611",
  "email": "carmen.ortiz@example.com",
  "has_license": true,
  "location_id": "mx-mty-centro",
  "availability": [
    "full_time"
  ],
  "schedule": [
    "afternoon"
  ],
  "experience_years": 2.0,
  "platforms": [
    "rappi"
  ],
  "platform_other": null,
  "start_date": "2026-11-02",
  "late_start_days": null,
  "interview_mode": "onsite",
  "experience_unverified": null,
  "city": "Monterrey",
  "location_name": "Sazón Monterrey"
}
```

## Recruiter summary

- Carmen Ortiz Luna, de Mitras, Monterrey; tiene licencia vigente y 2 años de experiencia en entregas con Rappi.
- Disponible tiempo completo por la tarde y puede empezar el 2 de noviembre, que coincide con la fecha de inicio indicada.
- Preguntó si el puesto es de tiempo completo y si la sucursal tiene gimnasio; no se le pudo confirmar, puede preguntarlo en la entrevista.

Summary flags: Preguntó por el sueldo; se le informó de 2,100 a 2,600 MXN semanales por jornada completa más propinas aparte., Preguntó si el seguro cubre accidentes manejando; se le respondió sobre IMSS y seguro de la empresa.
