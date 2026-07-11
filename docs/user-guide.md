# LecturaBot User Guide / Guía de LecturaBot

LecturaBot organizes pronunciation practice in a Discord voice channel. A
learner reads aloud in their target language while native speakers listen and
submit corrections. The bot manages the queue, reading texts, turns,
corrections, and reading-time statistics.

- [English guide](#english)
- [Guía en español](#español)

## English

### Quick start

1. Join a **Lectura voice channel**.
2. Open the text channel with the same room number.
3. Type `/lecturatest` in that text channel.
4. Use the new queue panel that appears in the channel and click
   **Unirse / Enter**.
5. Wait for at least two people to join the queue, then click
   **Comenzar Lectura / Start Reading**.
6. When the bot mentions you, choose a language and level or submit your own
   text.
7. Read the displayed passage aloud. Other people in the voice channel can
   submit pronunciation corrections.
8. Review the corrections, then click **Pasar turno / Pass Turn**. Only the
   current reader can use this button. The bot moves to the next reader and
   publishes a fresh queue panel.

### Join the correct room

The voice and text channels must match. For example, use the text channel for
Lectura 2 while connected to the Lectura 2 voice channel.

You must remain in that voice channel while queued. If you leave or move to a
different voice channel, the bot automatically removes you from the queue. You
lose your position and must join again at the end. The bot publishes a fresh
queue panel after any queue departure. If the current reader leaves, the bot
passes to the next reader when the session can continue.

### Queue controls

| Control | What it does |
| --- | --- |
| **Unirse / Enter** | Adds you to the end of the reader queue. |
| **Salir / Leave** | Removes you from the queue. |
| **Instrucciones / Instructions** | Shows a short private reminder of the workflow. |
| **Comenzar Lectura / Start Reading** | Starts the rotation when at least two queued participants are ready. |

`/lecturatest` publishes a fresh public queue panel in the channel. Running it
again replaces the previous panel instead of creating a separate session. This
temporary command avoids a name conflict while the original reading bot is
still active.

Each room's queue can contain up to 25 people.

### Choose what to read

When it is your turn, the bot mentions you and displays the text picker.

Catalog choices are available in both languages:

- **Español Principiante**, **Español Intermedio**, or **Español Avanzado**
- **English Beginner**, **English Intermediate**, or **English Advanced**

The shared catalog is bundled with the bot from the community reading-text
document, so choosing a passage does not open Google Docs or require an external
link.

To bring your own passage, choose one of these instead:

- **Tu propio texto / Your own text - Español**
- **Tu propio texto / Your own text - English**

Paste the passage into the form and submit it. Custom passages are used only
for that turn and are not added to the shared catalog. A custom passage can
contain up to 1,600 characters.

In the **Other Languages** room, use **Your own text** and enter the language
name when prompted. Catalog texts are only available in the standard
English-Spanish rooms.

If you do not want to read during your turn, click **Pasar Turno / Pass Turn**
on the text picker.

### Submit corrections

You must be listening in the matching voice channel, but you do **not** need to
join the reader queue to correct someone. The current reader cannot submit
corrections to their own reading.

Use either method:

1. Click **Poner Correcciones / Submit Corrections** on the reading message.
   Enter one corrected word or phrase per line.
2. Reply directly to the bot's reading message. Each non-empty line in your
   reply is treated as a separate correction.

Each correction can contain up to 100 characters. A reading can hold up to 20
correction entries and 1,400 correction characters in total. If a reply is not
accepted, the bot does not post a public error; use the correction button to
receive a private explanation.

The bot groups corrections by corrector. When a submitted word or phrase
matches the passage, the bot highlights every occurrence without changing the
passage's capitalization. Spelling and punctuation matter; unmatched
suggestions remain in the correction list but are not highlighted. If another
corrector later submits a word or phrase that has already been suggested, the
later duplicate remains attributed to that corrector but appears as
`~~struck through~~` to show that it was discarded.

Please submit corrections only when the reader is practicing your native
language. This is a community rule even though the bot does not currently
verify native-language roles.

### Finish or skip a turn

- The **current reader** clicks **Pasar turno / Pass Turn** after reviewing the
  corrections. No other participant can use Pass Turn. A completed reading is
  added to that reader's statistics.
- To force an absent reader's turn to end, use the separate **Saltar turno
  ausente / Skip AFK Turn** button. Exactly three different queued,
  non-current readers must vote. A person can vote only once per turn, and the
  three-vote requirement is not reduced when fewer eligible voters are
  present.
- The current reader cannot vote to skip their own turn. Correctors who are
  listening but are not in the queue cannot vote either.
- Skipped, disconnected, and selection-only turns do not count as completed
  readings.

After any normal pass, successful AFK skip, or current-reader departure, the
bot publishes a fresh queue panel for the new turn.

If the queue falls below two people, the session pauses. Once enough people
return, a queued participant must press **Start Reading** again.

### Queue statistics

The queue panel shows:

- numbered upcoming-turn positions: `1` is the current reader, `2` is next,
  and the remaining numbers follow the reading rotation
- `turns`: the number of readings you completed normally
- `avg reading time`: your average completed reading time in `MM:SS`
- `n/a`: no completed reading is available yet

Reading time begins when the bot publishes the selected passage and ends when
the current reader presses **Pass Turn**. The newly completed reading and its
updated statistics appear in the fresh queue panel for the next turn.

### Common problems

| Message or symptom | What to do |
| --- | --- |
| Join the matching voice channel first | Move to the voice channel paired with the current text channel. |
| This queue panel is no longer active | Run `/lecturatest` again and use the new queue panel. |
| That picker, reading, or turn is no longer active | Use the newest message for the current reader. |
| You are already in the queue | Continue waiting; do not press Enter again. |
| You already voted | Your skip vote was recorded and cannot be submitted twice. |
| An AFK skip does not advance | Three different queued, non-current readers are required; the threshold is not reduced. |
| The correction list is full | Give any remaining pronunciation feedback aloud in the voice channel. |
| Catalog choices fail in Other Languages | Use **Your own text** and provide the language name. |

Queue panels identify separate contacts for **Bot not working?** and **Found a
bug or text issue?**. Use the first for availability problems and the second for
behavior or reading-text problems.

## Español

### Inicio rápido

1. Únete a un **canal de voz de Lectura**.
2. Abre el canal de texto que tenga el mismo número.
3. Escribe `/lecturatest` en ese canal de texto.
4. Usa el panel nuevo de la cola que aparece en el canal y pulsa
   **Unirse / Enter**.
5. Espera hasta que haya al menos dos personas en la cola y pulsa
   **Comenzar Lectura / Start Reading**.
6. Cuando el bot te mencione, elige un idioma y nivel o proporciona tu propio
   texto.
7. Lee el texto en voz alta. Las otras personas del canal de voz pueden enviar
   correcciones de pronunciación.
8. Revisa las correcciones y pulsa **Pasar turno / Pass Turn**. Solo el lector
   actual puede usar este botón. El bot pasará al siguiente lector y publicará
   un panel nuevo de la cola.

### Usa la sala correcta

Los canales de voz y texto deben corresponder. Por ejemplo, usa el canal de
texto de Lectura 2 mientras estás conectado al canal de voz Lectura 2.

Debes permanecer en ese canal de voz mientras estés en la cola. Si sales o te
mueves a otro canal de voz, el bot te quitará automáticamente de la cola.
Perderás tu puesto y tendrás que unirte de nuevo al final. El bot publicará un
panel nuevo después de cualquier salida de la cola. Si sale el lector actual,
el bot pasará al siguiente lector cuando la sesión pueda continuar.

### Controles de la cola

| Control | Función |
| --- | --- |
| **Unirse / Enter** | Te añade al final de la cola de lectores. |
| **Salir / Leave** | Te quita de la cola. |
| **Instrucciones / Instructions** | Muestra en privado un recordatorio breve del proceso. |
| **Comenzar Lectura / Start Reading** | Comienza la rotación cuando haya al menos dos participantes preparados. |

`/lecturatest` publica un panel nuevo y público de la cola en el canal. Volver a
ejecutarlo reemplaza el panel anterior; no crea una sesión separada. Este
comando temporal evita un conflicto de nombres mientras el bot de lectura
original siga activo.

La cola de cada sala puede contener hasta 25 personas.

### Elige qué leer

Cuando sea tu turno, el bot te mencionará y mostrará el selector de textos.

El catálogo ofrece tres niveles en ambos idiomas:

- **Español Principiante**, **Español Intermedio** o **Español Avanzado**
- **English Beginner**, **English Intermediate** o **English Advanced**

El catálogo compartido está incluido dentro del bot a partir del documento
comunitario de textos; elegir un texto no abre Google Docs ni requiere un enlace
externo.

Para usar tu propio texto, elige:

- **Tu propio texto / Your own text - Español**
- **Tu propio texto / Your own text - English**

Pega el texto en el formulario y envíalo. Los textos propios solo se usan
durante ese turno y no se añaden al catálogo compartido. Un texto propio puede
tener hasta 1.600 caracteres.

En la sala **Other Languages**, usa **Your own text** e indica el nombre del
idioma cuando el bot lo pida. El catálogo solo está disponible en las salas
normales de inglés y español.

Si no quieres leer durante tu turno, pulsa **Pasar Turno / Pass Turn** en el
selector de textos.

### Envía correcciones

Debes estar escuchando en el canal de voz correspondiente, pero **no** necesitas
estar en la cola de lectores para corregir. El lector actual no puede corregir
su propia lectura.

Puedes usar cualquiera de estos métodos:

1. Pulsa **Poner Correcciones / Submit Corrections** en el mensaje de lectura.
   Escribe una palabra o frase corregida por línea.
2. Responde directamente al mensaje de lectura del bot. Cada línea no vacía de
   tu respuesta se considera una corrección separada.

Cada corrección puede tener hasta 100 caracteres. Una lectura puede contener
un máximo total de 20 correcciones y 1.400 caracteres de corrección. Si el bot
no acepta una respuesta, no publicará un error en el canal; usa el botón de
correcciones para recibir una explicación privada.

El bot agrupa las correcciones por corrector. Cuando una palabra o frase
coincide con el texto, el bot destaca todas sus apariciones sin cambiar las
mayúsculas del texto original. La ortografía y la puntuación importan; una
sugerencia que no coincida seguirá apareciendo en la lista de correcciones,
pero no será destacada en el texto. Si otro corrector envía después una palabra
o frase que ya había sido sugerida, la corrección posterior seguirá atribuida
a esa persona, pero aparecerá `~~tachada~~` para indicar que fue descartada.

Envía correcciones solamente cuando el lector esté practicando tu idioma
nativo. Esta es una regla de la comunidad, aunque el bot todavía no comprueba
los roles de idioma nativo.

### Termina o salta un turno

- El **lector actual** pulsa **Pasar turno / Pass Turn** después de revisar las
  correcciones. Ningún otro participante puede usar Pass Turn. La lectura
  completada se añade a sus estadísticas.
- Para forzar el fin del turno de una persona ausente, pulsa el botón separado
  **Saltar turno ausente / Skip AFK Turn**. Deben votar exactamente tres
  lectores distintos que estén en la cola y que no sean el lector actual. Cada
  persona puede votar una sola vez por turno y el requisito de tres votos no se
  reduce cuando haya menos votantes disponibles.
- El lector actual no puede votar para saltar su propio turno. Las personas que
  escuchan pero no están en la cola tampoco pueden votar.
- Los turnos saltados, las desconexiones y los turnos pasados antes de elegir
  un texto no cuentan como lecturas completadas.

Después de un turno pasado normalmente, un voto AFK completado o la salida del
lector actual, el bot publicará un panel nuevo de la cola para el siguiente
turno.

Si quedan menos de dos personas en la cola, la sesión se pausa. Cuando vuelva a
haber suficientes personas, alguien de la cola deberá pulsar **Start Reading**
otra vez.

### Estadísticas de la cola

El panel de la cola muestra:

- puestos numerados según los próximos turnos: `1` es el lector actual, `2` es
  el siguiente y los demás números siguen el orden de rotación
- `turns`: la cantidad de lecturas que completaste normalmente
- `avg reading time`: el promedio de tus lecturas completadas en formato
  `MM:SS`
- `n/a`: todavía no hay ninguna lectura completada

El tiempo comienza cuando el bot publica el texto elegido y termina cuando el
lector actual pulsa **Pass Turn**. La lectura recién completada y las
estadísticas actualizadas aparecerán en el panel nuevo del siguiente turno.

### Problemas frecuentes

| Mensaje o problema | Solución |
| --- | --- |
| Únete primero al canal de voz correspondiente | Entra al canal de voz asociado con el canal de texto actual. |
| Este panel de cola ya no está activo | Ejecuta `/lecturatest` otra vez y usa el panel nuevo. |
| Ese selector, texto o turno ya no está activo | Usa el mensaje más reciente del lector actual. |
| Ya estás en la cola | Sigue esperando; no vuelvas a pulsar Unirse. |
| Ya votaste | Tu voto fue registrado y no puedes enviarlo dos veces. |
| Un salto AFK no avanza | Se necesitan tres lectores distintos que estén en la cola y no sean el lector actual; el requisito no se reduce. |
| La lista de correcciones está llena | Comparte en voz cualquier corrección adicional. |
| Las opciones del catálogo fallan en Other Languages | Usa **Your own text** e indica el nombre del idioma. |

Los paneles de la cola muestran contactos distintos para **Bot not working?** y
**Found a bug or text issue?**. Usa el primero si el bot no está disponible y el
segundo para problemas de comportamiento o de los textos.
