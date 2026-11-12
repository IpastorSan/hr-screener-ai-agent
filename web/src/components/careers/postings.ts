import { LOCATIONS, type PostingLocation } from "./locations";

export type PostingCountry = "ES" | "MX";
export type PageLang = "es" | "en";

export type Posting = {
  country: PostingCountry;
  lang: PageLang;
  path: string;
  /** The same posting in the other language. */
  altPath: string;
  privacyPath: string;
  meta: { title: string; description: string };
  eyebrow: string;
  title: string;
  /** The end of `title`, set in italics. */
  titleAccent?: string;
  lead: string;
  /** Alt text for the hero, kitchen and dish photos, and the caption under the pair. */
  photos: { hero: string; kitchen: string; dish: string; caption: string };
  facts: { label: string; value: string; note: string }[];
  sections: { title: string; items: string[] }[];
  steps: { title: string; body: string }[];
  stepsTitle: string;
  citiesTitle: string;
  apply: {
    title: string;
    body: string;
    locationLabel: string;
    locationPlaceholder: string;
    anyLocation: string;
    button: string;
    aiNote: string;
    privacyLink: string;
    voiceLink: string;
  };
  locations: readonly PostingLocation[];
};

export const POSTING_PATHS = {
  es: { ES: "/empleo/repartidor-es", MX: "/empleo/repartidor-mx" },
  en: { ES: "/en/jobs/delivery-driver-es", MX: "/en/jobs/delivery-driver-mx" },
} as const;

export const PRIVACY_PATHS = { es: "/privacidad", en: "/en/privacy" } as const;

const base = (country: PostingCountry, lang: PageLang) => ({
  country,
  lang,
  path: POSTING_PATHS[lang][country],
  altPath: POSTING_PATHS[lang === "es" ? "en" : "es"][country],
  privacyPath: PRIVACY_PATHS[lang],
  locations: LOCATIONS[country],
});

const esES: Posting = {
  ...base("ES", "es"),
  meta: {
    title: "Repartidor/a en moto en España",
    description:
      "Empleo de repartidor/a en moto en Grupo Sazón: contrato laboral indefinido, 9,50 a 11 € brutos por hora más propinas, en Madrid, Barcelona, Valencia y 11 ciudades más.",
  },
  eyebrow: "Empleo · España",
  title: "Repartidor/a en moto",
  titleAccent: "en moto",
  lead: "Lleva los pedidos de nuestros restaurantes saliendo siempre del mismo local y dentro de su zona. Con contrato laboral en Grupo Sazón, sin plataformas de por medio.",
  photos: {
    hero: "Repartidor sonriendo sobre su moto frente a un local de Grupo Sazón al atardecer",
    kitchen: "Tres cocineros emplatando y riéndose en la cocina de un local",
    dish: "Plato de pollo a la parrilla con verduras y romesco sobre una mesa de madera",
    caption: "Lo que llevas no sale de una dark kitchen: sale de la cocina del local, con el equipo que lo cocina.",
  },
  facts: [
    { label: "Sueldo", value: "9,50–11 €/h", note: "brutos según ciudad y turno, más propinas" },
    { label: "Contrato", value: "Indefinido", note: "laboral, a jornada parcial o completa" },
    { label: "Horario", value: "Desde 12 h", note: "semanales, turnos de comida y cena" },
    { label: "Moto", value: "De empresa", note: "o la tuya, con un plus por kilómetro" },
  ],
  sections: [
    {
      title: "Qué ofrecemos",
      items: [
        "Alta en la Seguridad Social desde el primer día.",
        "Moto de empresa con gasolina, mantenimiento y seguro a todo riesgo; casco homologado y guantes incluidos.",
        "Uniforme, chaqueta impermeable y mochila térmica, gratis.",
        "Las propinas son íntegras para ti.",
        "Primer turno de formación pagado, acompañando a alguien del equipo.",
        "Cobro a mes vencido por transferencia.",
      ],
    },
    {
      title: "Qué necesitas",
      items: [
        "Carnet de conducir en vigor: A1, A2, A o B con más de 3 años para las motos de 125 cc, o AM para las de 49 cc.",
        "Vivir en una de nuestras zonas de reparto, en un radio de unos 4 km del local.",
        "Tener 18 años o más.",
        "DNI o NIE con permiso de trabajo y número de la Seguridad Social.",
        "Un smartphone con datos para la app de reparto.",
        "No hace falta experiencia.",
      ],
    },
  ],
  stepsTitle: "Cómo funciona",
  steps: [
    {
      title: "Chat de 3 minutos con Ana",
      body: "Ana es nuestra asistente de selección y es una IA. Te pregunta por tu carnet, tu zona y tu disponibilidad, y resuelve tus dudas.",
    },
    {
      title: "Entrevista en el local",
      body: "Si cumples los requisitos, reservas la entrevista en el mismo chat. Dura unos 15 minutos.",
    },
    {
      title: "Documentos y primer turno",
      body: "Entregas la documentación y haces un turno de formación pagado. Suelen pasar una o dos semanas desde la entrevista.",
    },
  ],
  citiesTitle: "Dónde buscamos gente",
  apply: {
    title: "Aplica en 3 minutos",
    body: "Elige el local que te quede más cerca o déjalo en blanco: Ana te preguntará dónde vives.",
    locationLabel: "Local",
    locationPlaceholder: "Cualquier local",
    anyLocation: "Cualquier local",
    button: "Aplicar",
    aiNote: "El chat lo atiende Ana, una asistente de IA.",
    privacyLink: "Aviso de privacidad",
    voiceLink: "o haz la entrevista por voz",
  },
};

const esMX: Posting = {
  ...base("MX", "es"),
  meta: {
    title: "Repartidor/a en moto en México",
    description:
      "Empleo de repartidor/a en moto en Grupo Sazón: contrato por nómina con prestaciones de ley, de 2,100 a 2,600 pesos a la semana más propinas, en CDMX, Guadalajara, Monterrey y más.",
  },
  eyebrow: "Empleo · México",
  title: "Repartidor/a en moto",
  titleAccent: "en moto",
  lead: "Lleve los pedidos de nuestros restaurantes saliendo siempre de la misma sucursal y dentro de su zona. Con contrato por nómina en Grupo Sazón, sin plataformas de por medio.",
  photos: {
    hero: "Repartidor sonriendo sobre su moto frente a una sucursal de Grupo Sazón al atardecer",
    kitchen: "Tres cocineros emplatando y riéndose en la cocina de una sucursal",
    dish: "Plato de pollo a la parrilla con verduras y romesco sobre una mesa de madera",
    caption: "Lo que lleva no sale de una dark kitchen: sale de la cocina de la sucursal, con el equipo que lo cocina.",
  },
  facts: [
    { label: "Sueldo", value: "$2,100–2,600", note: "MXN a la semana por jornada completa, más propinas" },
    { label: "Contrato", value: "Por nómina", note: "con IMSS, aguinaldo y vacaciones" },
    { label: "Horario", value: "Desde 12 h", note: "semanales, turnos de comida y cena" },
    { label: "Moto", value: "De la empresa", note: "o la suya, con pago por kilómetro" },
  ],
  sections: [
    {
      title: "Qué ofrecemos",
      items: [
        "Alta en el IMSS desde el primer día y prestaciones de ley: aguinaldo, vacaciones y prima vacacional.",
        "Moto de la empresa con gasolina, mantenimiento y seguro de cobertura amplia; casco y guantes incluidos.",
        "Uniforme, chamarra impermeable y mochila térmica, sin costo.",
        "Las propinas son íntegras para usted.",
        "Primer turno de capacitación pagado, acompañado por alguien del equipo.",
        "Pago semanal por depósito.",
      ],
    },
    {
      title: "Qué necesita",
      items: [
        "Licencia de conducir vigente que permita manejar motocicleta en su estado, por ejemplo la tipo A en Ciudad de México.",
        "Vivir en una de nuestras zonas de reparto, en un radio de unos 4 km de la sucursal.",
        "Tener 18 años o más.",
        "INE, CURP, RFC, número de seguridad social (NSS) y comprobante de domicilio reciente.",
        "Un celular con datos para la app de reparto.",
        "No se requiere experiencia.",
      ],
    },
  ],
  stepsTitle: "Cómo funciona",
  steps: [
    {
      title: "Chat de 3 minutos con Ana",
      body: "Ana es nuestra asistente de reclutamiento y es una IA. Le pregunta por su licencia, su zona y su disponibilidad, y resuelve sus dudas.",
    },
    {
      title: "Entrevista en la sucursal",
      body: "Si cumple los requisitos, agenda la entrevista en el mismo chat. Dura unos 15 minutos.",
    },
    {
      title: "Documentos y primer turno",
      body: "Entrega su documentación y hace un turno de capacitación pagado. Suelen pasar una o dos semanas desde la entrevista.",
    },
  ],
  citiesTitle: "Dónde buscamos personal",
  apply: {
    title: "Postúlese en 3 minutos",
    body: "Elija la sucursal que le quede más cerca o déjelo en blanco: Ana le preguntará dónde vive.",
    locationLabel: "Sucursal",
    locationPlaceholder: "Cualquier sucursal",
    anyLocation: "Cualquier sucursal",
    button: "Aplicar",
    aiNote: "El chat lo atiende Ana, una asistente de IA.",
    privacyLink: "Aviso de privacidad",
    voiceLink: "o haga la entrevista por voz",
  },
};

const enES: Posting = {
  ...base("ES", "en"),
  meta: {
    title: "Delivery rider in Spain",
    description:
      "Delivery rider jobs at Grupo Sazón in Spain: permanent employment contract, €9.50 to €11 gross per hour plus tips, in Madrid, Barcelona, Valencia and 11 more cities.",
  },
  eyebrow: "Jobs · Spain",
  title: "Delivery rider",
  titleAccent: "rider",
  lead: "Deliver our restaurants' orders, always from the same kitchen and within its area. You are employed by Grupo Sazón, with no delivery platform in between.",
  photos: {
    hero: "A smiling rider on his motorbike outside a Grupo Sazón restaurant at sunset",
    kitchen: "Three cooks plating up and laughing in a restaurant kitchen",
    dish: "Grilled chicken with vegetables and romesco on a wooden table",
    caption: "What you carry doesn't come from a dark kitchen: it comes from the restaurant's own kitchen and the team that cooks it.",
  },
  facts: [
    { label: "Pay", value: "€9.50–11/h", note: "gross, by city and shift, plus tips" },
    { label: "Contract", value: "Permanent", note: "employment contract, part or full time" },
    { label: "Hours", value: "From 12 h", note: "a week, lunch and dinner shifts" },
    { label: "Motorbike", value: "Provided", note: "or bring your own for a per-km allowance" },
  ],
  sections: [
    {
      title: "What we offer",
      items: [
        "Social Security registration from day one.",
        "Company motorbike with fuel, maintenance and comprehensive insurance; approved helmet and gloves included.",
        "Uniform, waterproof jacket and insulated backpack, free of charge.",
        "You keep all your tips.",
        "A paid first training shift alongside someone from the team.",
        "Paid monthly by bank transfer.",
      ],
    },
    {
      title: "What you need",
      items: [
        "A valid driving licence: A1, A2, A, or B held for over 3 years for the 125 cc bikes, or AM for the 49 cc ones.",
        "To live in one of our delivery areas, within about 4 km of the kitchen.",
        "To be 18 or older.",
        "A DNI or NIE with a work permit, and a Social Security number.",
        "A smartphone with data for the delivery app.",
        "No experience needed.",
      ],
    },
  ],
  stepsTitle: "How it works",
  steps: [
    {
      title: "A 3-minute chat with Ana",
      body: "Ana is our recruiting assistant and she is an AI. She asks about your licence, your area and your availability, and answers your questions.",
    },
    {
      title: "Interview at the kitchen",
      body: "If you meet the requirements, you book the interview in the same chat. It takes about 15 minutes.",
    },
    {
      title: "Paperwork and first shift",
      body: "You hand in your documents and do a paid training shift. It usually takes one to two weeks from the interview.",
    },
  ],
  citiesTitle: "Where we are hiring",
  apply: {
    title: "Apply in 3 minutes",
    body: "Pick the kitchen nearest to you, or leave it blank: Ana will ask where you live.",
    locationLabel: "Kitchen",
    locationPlaceholder: "Any kitchen",
    anyLocation: "Any kitchen",
    button: "Apply",
    aiNote: "The chat is run by Ana, an AI assistant.",
    privacyLink: "Privacy notice",
    voiceLink: "or do the interview by voice",
  },
};

const enMX: Posting = {
  ...base("MX", "en"),
  meta: {
    title: "Delivery driver in Mexico",
    description:
      "Delivery driver jobs at Grupo Sazón in Mexico: payroll contract with statutory benefits, 2,100 to 2,600 pesos a week plus tips, in Mexico City, Guadalajara, Monterrey and more.",
  },
  eyebrow: "Jobs · Mexico",
  title: "Delivery driver",
  titleAccent: "driver",
  lead: "Deliver our restaurants' orders, always from the same branch and within its area. You are on Grupo Sazón's payroll, with no delivery platform in between.",
  photos: {
    hero: "A smiling rider on his motorbike outside a Grupo Sazón branch at sunset",
    kitchen: "Three cooks plating up and laughing in a branch kitchen",
    dish: "Grilled chicken with vegetables and romesco on a wooden table",
    caption: "What you carry doesn't come from a dark kitchen: it comes from the branch's own kitchen and the team that cooks it.",
  },
  facts: [
    { label: "Pay", value: "MX$2,100–2,600", note: "a week full time, by city, plus tips" },
    { label: "Contract", value: "Payroll", note: "with IMSS, aguinaldo and paid holidays" },
    { label: "Hours", value: "From 12 h", note: "a week, lunch and dinner shifts" },
    { label: "Motorbike", value: "Provided", note: "or bring your own for a per-km allowance" },
  ],
  sections: [
    {
      title: "What we offer",
      items: [
        "IMSS registration from day one and statutory benefits: aguinaldo, paid holidays and holiday bonus.",
        "Company motorbike with fuel, maintenance and full-coverage insurance; helmet and gloves included.",
        "Uniform, waterproof jacket and insulated backpack, free of charge.",
        "You keep all your tips.",
        "A paid first training shift alongside someone from the team.",
        "Paid weekly by direct deposit.",
      ],
    },
    {
      title: "What you need",
      items: [
        "A valid driver's license that covers motorcycles in your state, such as a type A license in Mexico City.",
        "To live in one of our delivery areas, within about 4 km of the branch.",
        "To be 18 or older.",
        "INE, CURP, RFC, social security number (NSS) and a recent proof of address.",
        "A smartphone with data for the delivery app.",
        "No experience needed.",
      ],
    },
  ],
  stepsTitle: "How it works",
  steps: [
    {
      title: "A 3-minute chat with Ana",
      body: "Ana is our recruiting assistant and she is an AI. She asks about your license, your area and your availability, and answers your questions.",
    },
    {
      title: "Interview at the branch",
      body: "If you meet the requirements, you book the interview in the same chat. It takes about 15 minutes.",
    },
    {
      title: "Paperwork and first shift",
      body: "You hand in your documents and do a paid training shift. It usually takes one to two weeks from the interview.",
    },
  ],
  citiesTitle: "Where we are hiring",
  apply: {
    title: "Apply in 3 minutes",
    body: "Pick the branch nearest to you, or leave it blank: Ana will ask where you live.",
    locationLabel: "Branch",
    locationPlaceholder: "Any branch",
    anyLocation: "Any branch",
    button: "Apply",
    aiNote: "The chat is run by Ana, an AI assistant.",
    privacyLink: "Privacy notice",
    voiceLink: "or do the interview by voice",
  },
};

export const POSTINGS = { es: { ES: esES, MX: esMX }, en: { ES: enES, MX: enMX } } as const;

/** The chat URL with the posting's context; the chat reads `country`, `location_id` and `lang`. */
export function applyHref(posting: Pick<Posting, "country" | "lang">, locationId?: string | null): string {
  const params = new URLSearchParams({ country: posting.country });
  if (locationId) params.set("location_id", locationId);
  if (posting.lang === "en") params.set("lang", "en");
  return `/apply?${params.toString()}`;
}

/** The same posting context, for the voice interview. */
export function voiceHref(posting: Pick<Posting, "country" | "lang">, locationId?: string | null): string {
  return applyHref(posting, locationId).replace("/apply?", "/apply/voice?");
}
