"""When each delivery platform operated in each country: years of experience on a platform can't
exceed the years it has been there.

The earliest year counts (a pilot included), so the check only catches what can't be true.
A platform with no row for the country (Rappi in Spain: the candidate may have worked
elsewhere) is never checked. Sources, checked on 2026-10-20:

- Glovo ES 2015: founded and launched in Barcelona in early 2015 (Genbeta interview with
  co-founder Oscar Pierre, https://www.genbeta.com/herramientas/saldremos-a-por-capitales-europeas-a-partir-del-2016-hablamos-con-el-cofundador-de-glovo-oscar-pierre).
- Uber Eats ES 2015: a Barcelona pilot in 2015, closed that year; back in Madrid in January
  2017 (https://www.genbeta.com/a-fondo/ubereats-vuelve-a-espana-estas-son-sus-armas-y-lo-que-necesita-para-competir-con-just-eat-y-deliveroo).
- Just Eat ES 2010 (https://www.genbeta.com/web/quien-es-quien-en-el-sector-de-la-comida-a-domicilio-en-espana-y-en-el-mundo).
- Deliveroo ES 2015 to 29 November 2021, when it left Spain
  (https://www.merca2.es/2021/11/29/deliveroo-abandona-espana-seis-anos-actividad-812539/).
- Amazon Flex ES 2017 to April 2021 (https://www.genbeta.com/actualidad/como-funciona-amazon-flex-el-servicio-en-el-que-cualquier-autonomo-puede-ser-mensajero;
  Amazon: the programme has not operated in Spain since April 2021).
- Rappi MX 2015: three months after its founding in Colombia
  (https://dplnews.com/mexico-es-el-pais-mas-importante-para-rappi-teresa-gutierrez/).
- Uber Eats MX 2016: Mexico City, 6 October 2016 (https://www.excelsior.com.mx/hacker/2016/10/06/1120939).
- DiDi Food MX 2019: Guadalajara pilot from February 2019
  (https://www.xataka.com.mx/aplicaciones/didi-food-llega-a-cdmx-monterrey-empresa-china-va-uber-entregas-comida-a-domicilio).
- Amazon Flex MX 2023: a pilot in early 2023, official from 2024
  (https://www.xataka.com.mx/comercio-electronico/110-pesos-hora-al-entregar-paquetes-amazon-estos-sus-programas-para-repartidores-independientes-mexico).
"""

from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

from sazon.engine.models import Platform
from sazon.engine.validators import Country


class Operated(NamedTuple):
    since: int
    until: int | None = None


OPERATED: dict[tuple[Platform, Country], Operated] = {
    (Platform.GLOVO, "ES"): Operated(2015),
    (Platform.UBER_EATS, "ES"): Operated(2015),
    (Platform.JUST_EAT, "ES"): Operated(2010),
    (Platform.DELIVEROO, "ES"): Operated(2015, 2021),
    (Platform.AMAZON_FLEX, "ES"): Operated(2017, 2021),
    (Platform.RAPPI, "MX"): Operated(2015),
    (Platform.UBER_EATS, "MX"): Operated(2016),
    (Platform.DIDI_FOOD, "MX"): Operated(2019),
    (Platform.AMAZON_FLEX, "MX"): Operated(2023),
}


def max_years(platform: Platform, country: Country, today: date) -> int | None:
    """The most years anyone could have delivered with `platform` in `country`, counting the
    first and last years whole; None when there is no row."""
    operated = OPERATED.get((platform, country))
    if operated is None:
        return None
    last = min(operated.until or today.year, today.year)
    return max(last - operated.since + 1, 0)


def implausible_platform(
    years: float, platforms: Sequence[Platform] | None, country: Country, today: date
) -> Platform | None:
    """The platform the stated years can't fit, or None. With several platforms the one that
    has been around longest decides; a platform with no row means no check at all."""
    limits: list[tuple[int, Platform]] = []
    for platform in platforms or ():
        if platform == Platform.OTHER:
            continue
        limit = max_years(platform, country, today)
        if limit is None:
            return None
        limits.append((limit, platform))
    if not limits:
        return None
    longest, platform = max(limits, key=lambda item: item[0])
    return platform if years > longest else None
