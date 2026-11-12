import type { AskAnswer, Ui } from "@/lib/types";

export const candidateSamples: { prompt: string; ui: Ui }[] = [
  {
    prompt: "¿Tienes carné de conducir y moto propia?",
    ui: {
      kind: "quick_replies",
      options: [
        { value: "license_and_vehicle", label: "Sí, las dos cosas" },
        { value: "license_only", label: "Solo carné" },
        { value: "none", label: "Ninguna" },
      ],
      link: { href: "/privacidad", label: "Leer el aviso de privacidad" },
    },
  },
  {
    prompt: "¿Qué horarios te van bien: mañana, tarde, noche o flexible?",
    ui: {
      kind: "multi_select",
      min: 1,
      exclusive: ["flexible"],
      options: [
        { value: "morning", label: "Mañana" },
        { value: "afternoon", label: "Tarde" },
        { value: "evening", label: "Noche" },
        { value: "flexible", label: "Flexible" },
      ],
    },
  },
  {
    prompt: "¿Qué franjas te vienen bien? Elige todas las que puedas cubrir.",
    ui: {
      kind: "multi_select",
      min: 1,
      max: 4,
      options: [
        { value: "weekday_lunch", label: "Mediodías entre semana" },
        { value: "weekday_dinner", label: "Cenas entre semana" },
        { value: "weekend_lunch", label: "Mediodías de fin de semana" },
        { value: "weekend_dinner", label: "Cenas de fin de semana" },
        { value: "late_night", label: "Madrugada" },
      ],
    },
  },
  {
    prompt: "Estas son las entrevistas libres en tu zona:",
    ui: {
      kind: "slots",
      slots: [
        { id: "slot_01", startsAt: "2026-10-21T10:00:00+02:00", label: "10:00", location: "Sazón Chamberí, C/ Fuencarral 120" },
        { id: "slot_02", startsAt: "2026-10-21T17:30:00+02:00", label: "17:30", location: "Sazón Chamberí, C/ Fuencarral 120" },
        { id: "slot_03", startsAt: "2026-10-22T11:00:00+02:00", label: "11:00", location: "Sazón Lavapiés, C/ Argumosa 9" },
      ],
    },
  },
  {
    prompt: "Gracias, ya tengo todo. ¿Prefieres hacer la entrevista en persona o por videollamada?",
    ui: {
      kind: "quick_replies",
      options: [
        { value: "onsite", label: "En persona" },
        { value: "video", label: "Videollamada" },
      ],
    },
  },
  {
    prompt:
      "Listo, tu entrevista por videollamada con el equipo de selección es el jueves 22 de octubre a las 11:00. Te llegará una invitación con el enlace de Google Meet a tu correo, y también lo tienes aquí abajo.",
    ui: {
      kind: "booking",
      slotId: "slot_04",
      startsAt: "2026-10-22T11:00:00+02:00",
      label: "jueves 22 de octubre a las 11:00",
      location: "Sazón Barcelona Gràcia",
      address: "Carrer de Verdi 45, 08012 Barcelona",
      mode: "video",
      meetUrl: "https://meet.google.com/abc-defg-hij",
    },
  },
  {
    prompt: "Listo, tu entrevista es el jueves 22 de octubre a las 11:00 en Sazón Barcelona Gràcia.",
    ui: {
      kind: "booking",
      slotId: "slot_03",
      startsAt: "2026-10-22T11:00:00+02:00",
      label: "jueves 22 de octubre a las 11:00",
      location: "Sazón Barcelona Gràcia",
      address: "Carrer de Verdi 45, 08012 Barcelona",
    },
  },
  {
    prompt: "¿Cuál es tu número de móvil?",
    ui: {
      kind: "phone",
      countries: [
        { code: "ES", dial: "+34", label: "ES +34" },
        { code: "MX", dial: "+52", label: "MX +52" },
      ],
      selected: "ES",
    },
  },
  {
    prompt: "Repasemos tus datos antes de terminar:",
    ui: {
      kind: "confirm",
      fields: [
        { label: "Nombre", value: "Lucía Herrera" },
        { label: "Ciudad", value: "Madrid" },
        { label: "Vehículo", value: "Moto propia (125 cc)" },
        { label: "Disponibilidad", value: "Cenas y fines de semana" },
        { label: "Entrevista", value: "Mar 21 oct, 17:30 · Chamberí" },
      ],
    },
  },
];

export const recruiterSample: AskAnswer = {
  text: "Esta semana entraron 128 candidatos; 31 ya tienen entrevista. Madrid concentra la mitad del volumen.",
  insights: [
    { kind: "stat", label: "Candidatos", value: 128, hint: "últimos 7 días" },
    { kind: "stat", label: "Entrevistas", value: 31, delta: 8, hint: "frente a la semana pasada" },
    { kind: "stat", label: "Contratados", value: 9, delta: -2 },
    {
      kind: "candidates",
      title: "Últimos candidatos",
      candidates: [
        { id: "c_104", name: "Lucía Herrera", city: "Madrid", status: "qualified", experience_years: 2, start_date: "2026-10-20" },
        { id: "c_098", name: "Diego Ramírez", city: "Ciudad de México", status: "qualified", experience_years: 0.5, start_date: "2026-10-22" },
        { id: "c_087", name: "Marta Soler", city: "Valencia", status: "in_progress", experience_years: null, start_date: null },
        { id: "c_081", name: "Jorge Pineda", city: "Guadalajara", status: "abandoned", experience_years: 4, start_date: null },
        { id: "c_075", name: "Ana Beltrán", city: "Madrid", status: "disqualified", experience_years: 0, start_date: null },
      ],
    },
    {
      kind: "bar",
      title: "Candidatos por ciudad",
      data: [
        { label: "Madrid", value: 64 },
        { label: "CDMX", value: 31 },
        { label: "Valencia", value: 18 },
        { label: "Guadalajara", value: 15 },
      ],
    },
    {
      kind: "funnel",
      title: "Embudo de la semana",
      steps: [
        { stage: "Contacto", count: 128 },
        { stage: "Criba completa", count: 74 },
        { stage: "Entrevista", count: 31 },
        { stage: "Contratado", count: 9 },
      ],
    },
  ],
};
