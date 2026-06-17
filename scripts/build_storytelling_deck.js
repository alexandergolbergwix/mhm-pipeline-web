const fs = require('node:fs');
const path = require('node:path');

function requireModule(...candidates) {
  for (const mod of candidates) {
    try {
      return require(mod);
    } catch (_) {
      // try next candidate
    }
  }
  throw new Error(`Missing dependency. Install with: yarn add -D ${candidates[0]}`);
}

const PptxGenJS = requireModule(
  'pptxgenjs',
  path.join(__dirname, '../node_modules/pptxgenjs'),
  '/tmp/pptx-test/node_modules/pptxgenjs',
);
const JSZip = requireModule(
  'jszip',
  path.join(__dirname, '../node_modules/jszip'),
  '/tmp/pptx-test/node_modules/jszip',
);

const pptx = new PptxGenJS();

pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'Codex';
pptx.company = 'mhm-pipeline-web';
pptx.subject = 'Storytelling in academic and scientific presentations';
pptx.title = 'Turn Your Academic Talk Into a Story';
pptx.lang = 'en-US';
pptx.theme = {
  headFontFace: 'Aptos Display',
  bodyFontFace: 'Aptos',
};

const W = 13.333;
const H = 7.5;

const COLORS = {
  navy: '0B1220',
  slate: '111827',
  ink: '1F2937',
  muted: '64748B',
  paper: 'F7F5EE',
  paper2: 'FCFAF5',
  cream: 'FFF8EE',
  line: 'E7E1D6',
  coral: 'F97316',
  coral2: 'FB7185',
  gold: 'F4B942',
  teal: '14B8A6',
  sky: '38BDF8',
  green: '22C55E',
  darkCard: '121B2E',
};

function addBg(slide, dark = false) {
  slide.background = { color: dark ? COLORS.navy : COLORS.paper };
  if (dark) {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: 9.55, y: -0.9, w: 5.2, h: 5.2,
      line: { color: COLORS.coral, transparency: 100 },
      fill: { color: COLORS.coral, transparency: 84 },
    });
    slide.addShape(pptx.ShapeType.ellipse, {
      x: -1.1, y: 5.25, w: 4.6, h: 4.6,
      line: { color: COLORS.teal, transparency: 100 },
      fill: { color: COLORS.teal, transparency: 88 },
    });
    slide.addShape(pptx.ShapeType.rect, {
      x: 0, y: 0, w: W, h: 0.14,
      line: { color: COLORS.coral, transparency: 100 },
      fill: { color: COLORS.coral, transparency: 70 },
    });
  } else {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: 10.2, y: -1.0, w: 4.5, h: 4.5,
      line: { color: COLORS.coral, transparency: 100 },
      fill: { color: COLORS.coral, transparency: 92 },
    });
    slide.addShape(pptx.ShapeType.ellipse, {
      x: -1.0, y: 5.6, w: 4.4, h: 4.4,
      line: { color: COLORS.teal, transparency: 100 },
      fill: { color: COLORS.teal, transparency: 94 },
    });
    slide.addShape(pptx.ShapeType.rect, {
      x: 0, y: 0, w: W, h: 0.08,
      line: { color: COLORS.coral, transparency: 100 },
      fill: { color: COLORS.coral, transparency: 20 },
    });
  }
}

function addFooter(slide, num, dark = false) {
  const c = dark ? 'D6DEE9' : COLORS.muted;
  slide.addText(`0${num}`, {
    x: 12.55, y: 7.05, w: 0.45, h: 0.2,
    fontFace: 'Aptos',
    fontSize: 9,
    color: c,
    align: 'right',
    margin: 0,
  });
  slide.addText('Storytelling for academic talks', {
    x: 0.65, y: 7.06, w: 2.8, h: 0.16,
    fontFace: 'Aptos',
    fontSize: 8,
    color: c,
    margin: 0,
  });
}

function addKicker(slide, text, dark = false) {
  slide.addText(text.toUpperCase(), {
    x: 0.72, y: 0.46, w: 2.8, h: 0.2,
    fontFace: 'Aptos',
    fontSize: 10,
    bold: true,
    charSpace: 1.2,
    color: dark ? 'C9D4E2' : COLORS.coral,
    margin: 0,
  });
}

function addTitle(slide, title, dark = false, width = 8.6) {
  slide.addText(title, {
    x: 0.68, y: 0.76, w: width, h: 0.78,
    fontFace: 'Aptos Display',
    fontSize: 24,
    bold: true,
    color: dark ? 'FFFFFF' : COLORS.ink,
    margin: 0,
    fit: 'shrink',
  });
}

function addSubtitle(slide, text, dark = false, y = 1.55, width = 8.9) {
  slide.addText(text, {
    x: 0.72, y, w: width, h: 0.38,
    fontFace: 'Aptos',
    fontSize: 13,
    color: dark ? 'D4DCE8' : COLORS.muted,
    margin: 0,
    fit: 'shrink',
  });
}

function addBadge(slide, text, x, y, w, fill, color = 'FFFFFF') {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h: 0.36,
    rectRadius: 0.07,
    line: { color: fill, transparency: 100 },
    fill: { color: fill },
  });
  slide.addText(text, {
    x, y: y + 0.03, w, h: 0.22,
    fontFace: 'Aptos',
    fontSize: 10,
    bold: true,
    color,
    align: 'center',
    margin: 0,
  });
}

function addCard(slide, x, y, w, h, opts) {
  const fill = opts.fill || COLORS.paper2;
  const title = opts.title || '';
  const body = opts.body || '';
  const accent = opts.accent || COLORS.coral;
  const titleColor = opts.titleColor || COLORS.ink;
  const bodyColor = opts.bodyColor || COLORS.muted;
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h,
    rectRadius: 0.08,
    line: { color: opts.border || COLORS.line, pt: 1 },
    fill: { color: fill },
  });
  slide.addShape(pptx.ShapeType.rect, {
    x, y, w: 0.13, h,
    line: { color: accent, transparency: 100 },
    fill: { color: accent },
  });
  slide.addText(title, {
    x: x + 0.22, y: y + 0.18, w: w - 0.34, h: 0.32,
    fontFace: 'Aptos Display',
    fontSize: 18,
    bold: true,
    color: titleColor,
    margin: 0,
    fit: 'shrink',
  });
  slide.addText(body, {
    x: x + 0.22, y: y + 0.58, w: w - 0.34, h: h - 0.76,
    fontFace: 'Aptos',
    fontSize: 11.5,
    color: bodyColor,
    margin: 0,
    breakLine: false,
    fit: 'shrink',
  });
}

function addQuotePanel(slide, x, y, w, h, text, accent, bg = COLORS.paper2) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h,
    rectRadius: 0.07,
    line: { color: COLORS.line, pt: 1 },
    fill: { color: bg },
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: x + 0.18, y: y + 0.18, w: 0.55, h: 0.08,
    line: { color: accent, transparency: 100 },
    fill: { color: accent },
  });
  slide.addText(text, {
    x: x + 0.22, y: y + 0.44, w: w - 0.42, h: h - 0.6,
    fontFace: 'Aptos Display',
    fontSize: 18,
    italic: true,
    color: COLORS.ink,
    margin: 0,
    valign: 'mid',
    fit: 'shrink',
  });
}

function addBigStep(slide, x, y, label, title, desc, fill, outline = COLORS.line) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w: 3.85, h: 1.55,
    rectRadius: 0.06,
    line: { color: outline, pt: 1 },
    fill: { color: fill },
  });
  slide.addText(label, {
    x: x + 0.18, y: y + 0.13, w: 0.72, h: 0.18,
    fontFace: 'Aptos',
    fontSize: 9,
    bold: true,
    color: COLORS.coral,
    margin: 0,
  });
  slide.addText(title, {
    x: x + 0.18, y: y + 0.31, w: 3.4, h: 0.28,
    fontFace: 'Aptos Display',
    fontSize: 17,
    bold: true,
    color: COLORS.ink,
    margin: 0,
  });
  slide.addText(desc, {
    x: x + 0.18, y: y + 0.64, w: 3.42, h: 0.6,
    fontFace: 'Aptos',
    fontSize: 11,
    color: COLORS.muted,
    margin: 0,
    fit: 'shrink',
  });
}

function addMiniLine(slide, x1, y1, x2, y2, color, width = 2) {
  slide.addShape(pptx.ShapeType.line, {
    x: x1, y: y1, w: x2 - x1, h: y2 - y1,
    line: { color, pt: width },
  });
}

function notes(text) {
  return text.trim();
}

const HEBREW_NOTES_FONT = 'Arial';
const HEBREW_NOTES_LANG = 'he-IL';

function hebrewNotesParagraphXml(text) {
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return [
    '<a:p>',
    '<a:pPr rtl="1" algn="r"/>',
    '<a:r>',
    `<a:rPr lang="${HEBREW_NOTES_LANG}" dirty="0">`,
    `<a:cs typeface="${HEBREW_NOTES_FONT}"/>`,
    `<a:ea typeface="${HEBREW_NOTES_FONT}"/>`,
    '</a:rPr>',
    `<a:t>${escaped}</a:t>`,
    '</a:r>',
    `<a:endParaRPr lang="${HEBREW_NOTES_LANG}" dirty="0"/>`,
    '</a:p>',
  ].join('');
}

function fixHebrewNotesSlideXml(xml) {
  return xml.replace(
    /(<p:sp>[\s\S]*?Notes Placeholder 2[\s\S]*?<p:txBody><a:bodyPr\/><a:lstStyle\/>)([\s\S]*?)(<\/p:txBody>)/,
    (match, prefix, body, suffix) => {
      const textMatch = body.match(/<a:t>([\s\S]*?)<\/a:t>/);
      if (!textMatch) return match;
      const paragraphs = textMatch[1]
        .split(/\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      if (paragraphs.length === 0) return match;
      return `${prefix}${paragraphs.map(hebrewNotesParagraphXml).join('')}${suffix}`;
    },
  );
}

async function fixHebrewSpeakerNotes(pptxPath) {
  const zip = await JSZip.loadAsync(fs.readFileSync(pptxPath));
  const noteFiles = Object.keys(zip.files).filter((name) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(name));
  await Promise.all(noteFiles.map(async (name) => {
    const xml = await zip.file(name).async('string');
    zip.file(name, fixHebrewNotesSlideXml(xml));
  }));
  const output = await zip.generateAsync({
    type: 'nodebuffer',
    compression: 'DEFLATE',
    compressionOptions: {level: 9},
  });
  fs.writeFileSync(pptxPath, output);
}

// Slide 1: cover
{
  const slide = pptx.addSlide();
  addBg(slide, true);
  slide.addText('Turn Your Academic Talk Into a Story', {
    x: 0.72, y: 1.12, w: 6.8, h: 1.3,
    fontFace: 'Aptos Display',
    fontSize: 29,
    bold: true,
    color: 'FFFFFF',
    margin: 0,
    fit: 'shrink',
  });
  slide.addText('A 10-minute plan for stronger engagement, cleaner slides, and a presentation people remember', {
    x: 0.75, y: 2.45, w: 6.4, h: 0.62,
    fontFace: 'Aptos',
    fontSize: 15,
    color: 'D6DEE9',
    margin: 0,
    fit: 'shrink',
  });
  addBadge(slide, 'Slides in English', 0.74, 3.35, 1.55, COLORS.teal);
  addBadge(slide, 'Speaker notes in Hebrew', 2.42, 3.35, 2.3, COLORS.coral);
  addBadge(slide, '10 minutes', 4.9, 3.35, 1.0, COLORS.gold, COLORS.navy);
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 8.5, y: 1.0, w: 3.85, h: 4.9,
    rectRadius: 0.08,
    line: { color: 'FFFFFF', transparency: 100 },
    fill: { color: COLORS.darkCard, transparency: 14 },
  });
  slide.addText('01', {
    x: 8.85, y: 1.22, w: 2.4, h: 1.2,
    fontFace: 'Aptos Display',
    fontSize: 48,
    bold: true,
    color: COLORS.coral,
    margin: 0,
  });
  slide.addText('Hook\nStructure\nVisuals\nMeaning', {
    x: 8.95, y: 2.55, w: 2.2, h: 2.0,
    fontFace: 'Aptos Display',
    fontSize: 18,
    bold: true,
    color: 'FFFFFF',
    margin: 0,
    breakLine: false,
  });
  slide.addText('Data does not speak for itself. The story does.', {
    x: 8.95, y: 4.95, w: 2.85, h: 0.6,
    fontFace: 'Aptos',
    fontSize: 13,
    italic: true,
    color: 'D6DEE9',
    margin: 0,
    fit: 'shrink',
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 8.95, y: 4.7, w: 2.45, h: 0,
    line: { color: COLORS.coral, pt: 2 },
  });
  addFooter(slide, 1, true);
  slide.addNotes(notes(`
אני פותח בשאלה: למה הרבה מצגות אקדמיות מרגישות כמו רשימת עובדות ולא כמו סיפור?
המטרה של המצגת היא להראות איך אפשר להפוך תוכן אקדמי למשהו הרבה יותר ברור, חי ומעניין, בלי לוותר על הדיוק.
אני לא מתחיל בפרטים הטכניים. אני מתחיל במסגרת שמחזיקה את כל המצגת.
`));
}

// Slide 2: ABT
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'The core structure');
  addTitle(slide, 'Start with conflict, not chronology');
  addSubtitle(slide, 'The ABT pattern keeps the talk moving: context, tension, resolution.');
  addBigStep(slide, 0.72, 1.98, 'AND', 'Context', 'Set the scene. Show what is already known and what the audience can agree on.', 'FFF8EE');
  addBigStep(slide, 4.75, 1.98, 'BUT', 'Conflict', 'Introduce the gap, contradiction, or problem that creates curiosity.', 'EEF8F6');
  addBigStep(slide, 8.78, 1.98, 'THEREFORE', 'Resolution', 'Show the method, result, and the implication that answers the question.', 'FFF2F2');
  addMiniLine(slide, 4.57, 2.74, 4.71, 2.74, COLORS.muted, 1.5);
  addMiniLine(slide, 8.6, 2.74, 8.74, 2.74, COLORS.muted, 1.5);
  addMiniLine(slide, 3.9, 4.05, 9.95, 4.05, COLORS.line, 1);
  addBadge(slide, 'Context', 1.44, 4.25, 0.95, COLORS.teal);
  addBadge(slide, 'Tension', 6.15, 4.25, 1.0, COLORS.coral);
  addBadge(slide, 'Resolution', 10.46, 4.25, 1.15, COLORS.gold, COLORS.navy);
  slide.addText('Good talks create a question in the audience, then pay it off.', {
    x: 2.1, y: 5.2, w: 9.2, h: 0.34,
    fontFace: 'Aptos Display',
    fontSize: 16,
    bold: true,
    color: COLORS.ink,
    align: 'center',
    margin: 0,
  });
  addFooter(slide, 2);
  slide.addNotes(notes(`
השלד הכי חשוב הוא And, But, Therefore.
קודם נותנים קונטקסט, אחר כך מציגים את הפער או המתח, ורק אז את הפתרון.
זה מונע מצגת כרונולוגית של "ואז, ואז, ואז", ומכריח אותי לבנות טיעון.
`));
}

// Slide 3: hook
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'Hook the room fast');
  addTitle(slide, 'The first 60 seconds decide everything');
  addSubtitle(slide, 'Open with a question, a surprise, or an immersive prompt.');
  slide.addShape(pptx.ShapeType.ellipse, {
    x: 0.92, y: 2.05, w: 2.0, h: 2.0,
    line: { color: COLORS.coral, pt: 2.5 },
    fill: { color: 'FFFFFF', transparency: 100 },
  });
  slide.addText('60', {
    x: 1.2, y: 2.48, w: 1.45, h: 0.56,
    fontFace: 'Aptos Display',
    fontSize: 34,
    bold: true,
    color: COLORS.coral,
    align: 'center',
    margin: 0,
  });
  slide.addText('seconds', {
    x: 1.12, y: 3.0, w: 1.6, h: 0.24,
    fontFace: 'Aptos',
    fontSize: 12,
    color: COLORS.muted,
    align: 'center',
    margin: 0,
  });
  addBadge(slide, 'Question', 3.45, 2.04, 1.0, COLORS.teal);
  addBadge(slide, 'Surprise', 6.05, 2.04, 0.95, COLORS.gold, COLORS.navy);
  addBadge(slide, 'Immersion', 8.55, 2.04, 1.0, COLORS.coral);
  addCard(slide, 3.42, 2.52, 2.1, 1.75, {
    fill: 'FFFDF8',
    title: 'Ask',
    body: 'A sharp question pulls the audience into the problem before they can drift away.',
    accent: COLORS.teal,
  });
  addCard(slide, 5.92, 2.52, 2.1, 1.75, {
    fill: 'FFFDF8',
    title: 'Startle',
    body: 'A counterintuitive fact creates useful cognitive friction and makes people pay attention.',
    accent: COLORS.gold,
  });
  addCard(slide, 8.42, 2.52, 2.1, 1.75, {
    fill: 'FFFDF8',
    title: 'Invite',
    body: 'A quick mental image or action moves the audience from passive listening to participation.',
    accent: COLORS.coral,
  });
  addQuotePanel(slide, 3.45, 5.0, 7.35, 1.0, 'Skip the long introduction. Start with the problem.', COLORS.coral);
  addFooter(slide, 3);
  slide.addNotes(notes(`
בדקה הראשונה אני לא מציג את עצמי בצורה ארוכה, אלא פותח עם שאלה, עובדה מפתיעה או דימוי שמכניס את הקהל ישר פנימה.
אם הקהל לא נכנס כבר בהתחלה, קשה מאוד להחזיר את תשומת הלב שלו אחר כך.
כאן אני רוצה להראות שהפתיחה היא לא הקדמה טכנית, אלא חלק מהסיפור.
`));
}

// Slide 4: one claim per slide
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'Design for one claim');
  addTitle(slide, 'Let the slide title say the conclusion');
  addSubtitle(slide, 'A slide should be readable as a sentence, not just a topic label.');
  addCard(slide, 0.82, 1.95, 5.55, 3.85, {
    fill: 'FFF7F4',
    title: 'Bad: topic label plus clutter',
    body: 'Too many bullets\nToo much text\nNo clear takeaway\nAudience reads instead of listens',
    accent: COLORS.coral2,
  });
  for (let i = 0; i < 4; i += 1) {
    slide.addShape(pptx.ShapeType.rect, {
      x: 1.18, y: 2.72 + (i * 0.58), w: 3.55, h: 0.12,
      line: { color: COLORS.muted, transparency: 100 },
      fill: { color: 'CBD5E1' },
    });
  }
  slide.addShape(pptx.ShapeType.line, {
    x: 1.1, y: 2.5, w: 3.8, h: 2.1,
    line: { color: COLORS.coral2, pt: 3, beginArrowType: 'none', endArrowType: 'none' },
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 4.92, y: 2.5, w: -3.82, h: 2.1,
    line: { color: COLORS.coral2, pt: 3, beginArrowType: 'none', endArrowType: 'none' },
  });
  addCard(slide, 6.93, 1.95, 5.55, 3.85, {
    fill: 'F6FBFA',
    title: 'Good: one claim, one visual',
    body: 'The title already gives the conclusion.\nThe visual proves it.\nThe speech explains why it matters.',
    accent: COLORS.teal,
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 7.28, y: 2.62, w: 4.7, h: 1.95,
    rectRadius: 0.05,
    line: { color: COLORS.line, pt: 1 },
    fill: { color: 'FFFFFF' },
  });
  addMiniLine(slide, 7.62, 4.1, 11.45, 4.1, COLORS.teal, 2);
  addMiniLine(slide, 7.8, 3.95, 8.35, 3.45, COLORS.teal, 2);
  addMiniLine(slide, 8.35, 3.45, 9.06, 3.82, COLORS.teal, 2);
  addMiniLine(slide, 9.06, 3.82, 9.85, 3.2, COLORS.teal, 2);
  addMiniLine(slide, 9.85, 3.2, 10.62, 3.55, COLORS.teal, 2);
  addMiniLine(slide, 10.62, 3.55, 11.35, 2.95, COLORS.teal, 2);
  slide.addText('Your slide title should already tell the conclusion.', {
    x: 7.52, y: 2.9, w: 4.25, h: 0.35,
    fontFace: 'Aptos Display',
    fontSize: 16,
    bold: true,
    color: COLORS.ink,
    align: 'center',
    margin: 0,
    fit: 'shrink',
  });
  slide.addText('Then the chart, diagram, or image becomes evidence instead of decoration.', {
    x: 7.6, y: 4.42, w: 4.2, h: 0.36,
    fontFace: 'Aptos',
    fontSize: 11,
    color: COLORS.muted,
    align: 'center',
    margin: 0,
    fit: 'shrink',
  });
  addBadge(slide, 'One claim', 5.72, 5.98, 0.98, COLORS.coral);
  addBadge(slide, 'One visual', 7.02, 5.98, 0.98, COLORS.teal);
  addBadge(slide, 'One takeaway', 8.32, 5.98, 1.16, COLORS.gold, COLORS.navy);
  addFooter(slide, 4);
  slide.addNotes(notes(`
כל שקף צריך לשרת רעיון אחד.
הכותרת עצמה צריכה להגיד את המסקנה, ולא רק להיות שם של נושא.
אם יש יותר מדי טקסט, הקהל מתחיל לקרוא במקום להקשיב.
`));
}

// Slide 5: evidence
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'Show the right evidence');
  addTitle(slide, 'Show only the evidence that matters');
  addSubtitle(slide, 'Exploratory work belongs behind the scenes. Explanatory work belongs on the slide.');
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.8, y: 2.0, w: 5.65, h: 3.85,
    rectRadius: 0.08,
    line: { color: COLORS.line, pt: 1 },
    fill: { color: 'FFF9F6' },
  });
  slide.addText('Exploratory analysis', {
    x: 1.04, y: 2.22, w: 2.1, h: 0.24,
    fontFace: 'Aptos Display',
    fontSize: 16,
    bold: true,
    color: COLORS.ink,
    margin: 0,
  });
  slide.addText('100 rocks', {
    x: 1.03, y: 2.64, w: 1.1, h: 0.22,
    fontFace: 'Aptos',
    fontSize: 12,
    bold: true,
    color: COLORS.muted,
    margin: 0,
  });
  for (let i = 0; i < 18; i += 1) {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: 1.0 + ((i % 6) * 0.68),
      y: 3.0 + (Math.floor(i / 6) * 0.78),
      w: 0.48, h: 0.38,
      line: { color: 'CBD5E1', pt: 1 },
      fill: { color: 'CBD5E1', transparency: 15 + (i % 3) * 8 },
    });
  }
  slide.addText('All the work', {
    x: 1.1, y: 5.35, w: 2.1, h: 0.22,
    fontFace: 'Aptos',
    fontSize: 11,
    color: COLORS.muted,
    margin: 0,
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 6.82, y: 2.0, w: 5.7, h: 3.85,
    rectRadius: 0.08,
    line: { color: COLORS.line, pt: 1 },
    fill: { color: 'F2FAF7' },
  });
  slide.addText('Explanatory story', {
    x: 7.08, y: 2.22, w: 2.0, h: 0.24,
    fontFace: 'Aptos Display',
    fontSize: 16,
    bold: true,
    color: COLORS.ink,
    margin: 0,
  });
  slide.addText('2 gems', {
    x: 7.08, y: 2.64, w: 1.1, h: 0.22,
    fontFace: 'Aptos',
    fontSize: 12,
    bold: true,
    color: COLORS.teal,
    margin: 0,
  });
  slide.addShape(pptx.ShapeType.diamond, {
    x: 7.35, y: 3.05, w: 1.35, h: 1.55,
    line: { color: COLORS.teal, pt: 2 },
    fill: { color: '9EE7D6', transparency: 15 },
  });
  slide.addShape(pptx.ShapeType.diamond, {
    x: 9.2, y: 3.05, w: 1.35, h: 1.55,
    line: { color: COLORS.coral, pt: 2 },
    fill: { color: 'FBC2A1', transparency: 12 },
  });
  slide.addText('The two findings', {
    x: 7.1, y: 5.16, w: 2.0, h: 0.22,
    fontFace: 'Aptos',
    fontSize: 11,
    color: COLORS.muted,
    margin: 0,
  });
  slide.addText('Use fewer numbers, fewer bullets, and a sharper sentence.', {
    x: 7.05, y: 5.42, w: 4.8, h: 0.3,
    fontFace: 'Aptos Display',
    fontSize: 15,
    bold: true,
    color: COLORS.ink,
    margin: 0,
  });
  slide.addText('The audience should see the conclusion, not your entire scratchpad.', {
    x: 0.95, y: 6.25, w: 10.9, h: 0.25,
    fontFace: 'Aptos',
    fontSize: 11.5,
    italic: true,
    color: COLORS.muted,
    align: 'center',
    margin: 0,
  });
  addFooter(slide, 5);
  slide.addNotes(notes(`
כאן אני מדגיש את ההבדל בין חיפוש תוצאות לבין הסבר לקהל.
לא צריך להראות את כל מה שעשינו. צריך להראות רק את הדברים שבאמת תומכים בטענה המרכזית.
כשהשקף נהיה עמוס, הקהל לא יודע לאן להסתכל.
`));
}

// Slide 6: transitions
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'Keep attention moving');
  addTitle(slide, 'Transitions create momentum');
  addSubtitle(slide, 'Use verbal signposts, repeated visual motifs, and progressive reveal.');
  slide.addShape(pptx.ShapeType.line, {
    x: 1.08, y: 3.35, w: 10.95, h: 0,
    line: { color: COLORS.line, pt: 3 },
  });
  const nodes = [
    { x: 1.1, label: 'RECAP', color: COLORS.teal, title: 'What we just saw', body: 'Summarize the previous point in one short sentence.' },
    { x: 5.15, label: 'PIVOT', color: COLORS.coral, title: 'Now the tension', body: 'Say why the previous slide is not enough and what question comes next.' },
    { x: 9.2, label: 'REVEAL', color: COLORS.gold, title: 'Next the answer', body: 'Show the next result or explanation after the audience is ready.' },
  ];
  nodes.forEach((n, i) => {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: n.x + 1.15, y: 3.03, w: 0.64, h: 0.64,
      line: { color: n.color, pt: 2 },
      fill: { color: 'FFFFFF' },
    });
    slide.addText(String(i + 1), {
      x: n.x + 1.15, y: 3.18, w: 0.64, h: 0.2,
      fontFace: 'Aptos Display',
      fontSize: 14,
      bold: true,
      color: n.color,
      align: 'center',
      margin: 0,
    });
    slide.addText(n.label, {
      x: n.x, y: 2.25, w: 3.35, h: 0.2,
      fontFace: 'Aptos',
      fontSize: 9,
      bold: true,
      color: n.color,
      align: 'center',
      margin: 0,
    });
    addCard(slide, n.x, 3.85, 3.35, 1.75, {
      fill: 'FFFDF8',
      title: n.title,
      body: n.body,
      accent: n.color,
    });
  });
  addQuotePanel(slide, 1.08, 5.95, 11.1, 0.72, 'Pause for a beat after a key result. Silence can be a transition.', COLORS.coral, 'FFF9F6');
  addFooter(slide, 6);
  slide.addNotes(notes(`
מעברים טובים שומרים על תנופה.
אני מסכם בקצרה מה היה קודם, ואז מסמן בבירור לאן עוברים.
גם השפה וגם הוויזואליה צריכים לעבוד יחד, כדי שהקהל לא יאבד את החוט.
`));
}

// Slide 7: analogy + human stakes
{
  const slide = pptx.addSlide();
  addBg(slide, false);
  addKicker(slide, 'Make it concrete');
  addTitle(slide, 'Make the abstract concrete and the stakes human');
  addSubtitle(slide, 'A good analogy builds understanding. A good story makes the outcome matter.');
  addCard(slide, 0.82, 2.0, 5.42, 3.92, {
    fill: 'FFF9F7',
    title: 'Analogy',
    body: 'Start with something familiar.\nMap the parts carefully.\nSay where the comparison breaks.',
    accent: COLORS.teal,
  });
  slide.addShape(pptx.ShapeType.line, {
    x: 1.16, y: 3.07, w: 3.9, h: 0,
    line: { color: COLORS.teal, pt: 2 },
  });
  addBadge(slide, 'Familiar', 1.2, 3.45, 0.98, COLORS.teal);
  addBadge(slide, '→', 2.38, 3.45, 0.42, COLORS.paper2, COLORS.ink);
  addBadge(slide, 'New idea', 2.9, 3.45, 0.92, COLORS.coral);
  addBadge(slide, '→', 4.02, 3.45, 0.42, COLORS.paper2, COLORS.ink);
  addBadge(slide, 'Clear meaning', 4.56, 3.45, 1.18, COLORS.gold, COLORS.navy);
  addCard(slide, 6.96, 2.0, 5.42, 3.92, {
    fill: 'F4FBF8',
    title: 'Why it matters',
    body: 'Link the result to a real decision, a real audience, or a real consequence.\nThat is where memory and persuasion happen.',
    accent: COLORS.coral,
  });
  slide.addText('If the audience feels the significance, they will remember the science.', {
    x: 7.18, y: 4.86, w: 4.95, h: 0.5,
    fontFace: 'Aptos Display',
    fontSize: 15,
    bold: true,
    color: COLORS.ink,
    margin: 0,
    fit: 'shrink',
  });
  addQuotePanel(slide, 1.08, 6.22, 11.0, 0.55, 'Always say where the analogy breaks.', COLORS.coral2, 'FFF8F8');
  addFooter(slide, 7);
  slide.addNotes(notes(`
דימוי טוב עוזר לקהל להבין מהר, אבל חייבים להגיד איפה הוא מפסיק להיות מדויק.
בנוסף צריך לקשור את התוכן להשלכות אמיתיות: למה זה חשוב, למי זה משנה, ומה יוצא מזה.
כאן אני עובר מהסבר טכני למשמעות אנושית.
`));
}

// Slide 8: close
{
  const slide = pptx.addSlide();
  addBg(slide, true);
  slide.addText('What to change first', {
    x: 0.72, y: 1.1, w: 4.4, h: 0.5,
    fontFace: 'Aptos Display',
    fontSize: 28,
    bold: true,
    color: 'FFFFFF',
    margin: 0,
    fit: 'shrink',
  });
  slide.addText('A simple upgrade plan you can apply to almost any academic presentation.', {
    x: 0.76, y: 1.7, w: 5.8, h: 0.35,
    fontFace: 'Aptos',
    fontSize: 13,
    color: 'D6DEE9',
    margin: 0,
    fit: 'shrink',
  });
  addBigStep(slide, 0.76, 2.35, '1', 'Open with a question', 'Use tension, surprise, or an immersive prompt instead of a long introduction.', 'FFF8EE', '2A3246');
  addBigStep(slide, 4.84, 2.35, '2', 'Turn bullets into claims', 'Each slide should have one message, one visual, and one point of emphasis.', 'EEF8F6', '2A3246');
  addBigStep(slide, 8.92, 2.35, '3', 'End with the so what', 'Do not stop at the result. Explain why it matters and what the audience should remember.', 'FFF2F2', '2A3246');
  addQuotePanel(slide, 0.8, 5.4, 11.95, 0.95, 'A strong talk is clear enough to follow, and vivid enough to remember.', COLORS.gold, COLORS.darkCard);
  slide.addText('If they can retell your argument in one sentence, it worked.', {
    x: 1.0, y: 6.62, w: 11.1, h: 0.3,
    fontFace: 'Aptos Display',
    fontSize: 16,
    bold: true,
    color: 'FFFFFF',
    align: 'center',
    margin: 0,
  });
  addFooter(slide, 8, true);
  slide.addNotes(notes(`
הסיום צריך לענות על ה-So what.
אני מסכם בשלושה עקרונות פשוטים: להתחיל חזק, לבנות סיפור, ולהראות למה זה חשוב.
אם הקהל יכול לספר את הטיעון שלך במשפט אחד, המצגת עבדה.
`));
}

async function main() {
  const out = path.resolve(process.cwd(), 'storytelling_presentation_10min.pptx');
  await pptx.writeFile({fileName: out, compression: true});
  await fixHebrewSpeakerNotes(out);
  console.log(out);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
