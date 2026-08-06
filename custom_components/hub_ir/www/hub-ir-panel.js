/**
 * HubIR — learn a device's codes without leaving the browser.
 *
 * Plain DOM on purpose: no build step, no bundler, and nothing fetched from
 * outside, which Home Assistant's content security policy would block anyway.
 * Styling comes from Home Assistant's own CSS custom properties, so the panel
 * follows the user's theme.
 *
 * Anything worth testing — which codes a device needs, in what order, whether
 * the result is a valid device file — is decided by the Python side and asked
 * for over the websocket. This file is the hands, not the head.
 */

/**
 * The HubIR mark, inline because the content security policy blocks anything
 * this file would try to fetch. Kept in step with brand/hub-ir-icon.svg, which
 * is what the PNGs for home-assistant/brands are rendered from.
 */
const LOGO = `
<svg viewBox="0 0 256 256" width="28" height="28" aria-hidden="true">
  <defs>
    <linearGradient id="hubir-wave" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#FF6B35"/><stop offset="1" stop-color="#FFB627"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="56" fill="#14243A"/>
  <g fill="none" stroke="url(#hubir-wave)" stroke-linecap="round">
    <path d="M68 136 A 52 52 0 0 1 120 188" stroke-width="17"/>
    <path d="M68 92 A 96 96 0 0 1 164 188" stroke-width="16" opacity=".82"/>
    <path d="M68 48 A 140 140 0 0 1 208 188" stroke-width="15" opacity=".58"/>
  </g>
  <circle cx="68" cy="188" r="19" fill="url(#hubir-wave)"/>
</svg>`;

const PLATFORM_LABELS = {
  climate: "Air conditioner",
  fan: "Fan",
  light: "Light",
  media_player: "TV / media player",
  switch: "Switch or socket",
};

const DEFAULT_SPEC = {
  climate: {
    manufacturer: "",
    models: [],
    minTemperature: 16,
    maxTemperature: 30,
    precision: 1,
    temperatureUnit: "C",
    operationModes: ["cool", "heat"],
    fanModes: ["auto", "low", "mid", "high"],
    swingModes: [],
    hasOnCommand: false,
    modeOptions: {},
    presets: [],
    presetBaseline: {},
    extraCommands: [],
  },
  fan: {
    manufacturer: "",
    models: [],
    speed: ["low", "mid", "high"],
    hasDirection: false,
    hasOscillate: false,
    extraCommands: [],
  },
  light: {
    manufacturer: "",
    models: [],
    brightness: [],
    colorTemperature: [],
    hasNight: false,
    extraCommands: [],
  },
  media_player: {
    manufacturer: "",
    models: [],
    buttons: [
      "on",
      "off",
      "volumeUp",
      "volumeDown",
      "mute",
      "previousChannel",
      "nextChannel",
    ],
    sources: [],
    extraCommands: [],
  },
  switch: {
    manufacturer: "",
    models: [],
    hasToggle: false,
    extraCommands: [],
  },
};

const CLIMATE_MODES = ["cool", "heat", "dry", "fan_only", "auto", "heat_cool"];

/**
 * Every list the user builds by hand, described once.
 *
 * Order is load-bearing for most of these — a climate file's fan speeds and a
 * fan's speeds are matched against the command tree by position, not by name —
 * so the editor shows the position and lets it be changed, rather than burying
 * it in a comma-separated string where a reordering is invisible.
 *
 * `fill` is the one-click starting point offered while a list is still empty,
 * so nobody has to invent names for something as conventional as fan speeds.
 */
const LIST_FIELDS = {
  models: {
    label: "Models",
    one: "model",
    placeholder: "FTKC35",
    presets: [],
  },
  fanModes: {
    label: "Fan speeds",
    one: "fan speed",
    ordered: true,
    note: "Matched against the codes you capture, in this order.",
    presets: ["auto", "low", "mid", "medium", "high", "quiet", "turbo"],
    fill: ["auto", "low", "mid", "high"],
  },
  swingModes: {
    label: "Swing positions",
    one: "swing position",
    ordered: true,
    note: "Leave empty unless the swing setting is part of the unit's packets.",
    presets: ["auto", "1", "2", "3", "4", "5", "swing", "off"],
    fill: ["auto", "1", "2", "3"],
  },
  speed: {
    label: "Speeds",
    one: "speed",
    ordered: true,
    note: "Slowest first.",
    presets: ["low", "mid", "medium", "high", "lowest", "highest"],
    fill: ["low", "mid", "high"],
  },
  brightness: {
    label: "Brightness steps",
    one: "step",
    numeric: true,
    ordered: true,
    note: "Dimmest first, on Home Assistant's 1–255 scale.",
    presets: [10, 64, 128, 192, 255],
    fill: [10, 128, 255],
  },
  colorTemperature: {
    label: "Colour temperatures",
    one: "temperature",
    numeric: true,
    ordered: true,
    note: "In kelvin, warmest first.",
    presets: [2700, 3000, 4000, 5000, 6500],
    fill: [2700, 4000, 6500],
  },
  sources: {
    label: "Sources and channels",
    one: "source",
    presets: ["HDMI1", "HDMI2", "HDMI3", "AV", "TV", "USB"],
  },
  presets: {
    label: "One-touch buttons",
    one: "button",
    presets: ["turbo", "eco", "sleep", "quiet", "powerful", "comfort"],
  },
  extraCommands: {
    label: "Other buttons",
    one: "button",
    note: "Reachable from the hub_ir.send_command service, by name.",
    presets: [],
  },
};

/** Suggestions for the free-form button list, by what the device is. */
const EXTRA_PRESETS = {
  climate: ["display", "beep", "clean", "ionizer", "timer"],
  fan: ["timer", "natural", "light"],
  light: ["flash", "scene", "favourite"],
  media_player: [
    "menu",
    "home",
    "back",
    "exit",
    "up",
    "down",
    "left",
    "right",
    "ok",
    "info",
    "guide",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
  ],
  switch: ["input_cd", "input_aux", "volume_up", "volume_down"],
};

/**
 * The panel in other languages.
 *
 * English is not in here: every call site passes its English text as the
 * fallback argument, so a key that is missing — or a language nobody has
 * translated — reads as English rather than as a raw key. That also keeps the
 * English wording next to the markup it belongs to, where it can be read.
 *
 * Only prose is translated. Mode names, fan speeds, source names and the
 * media player's button keys are keys in the device file, so they are left
 * exactly as they are wherever they appear — translating them in one screen
 * and not the next is how a capture session stops making sense.
 */
const STRINGS = {
  vi: {
    // Setup, card 1
    "step.teaching": "Bạn đang dạy thiết bị gì?",
    "label.deviceType": "Loại thiết bị",
    "label.remote": "Bộ điều khiển Broadlink",
    "label.newDeviceCode": "Mã thiết bị mới",
    "error.noRemote":
      "Không tìm thấy bộ điều khiển Broadlink nào. Hãy cài đặt tích hợp " +
      "Broadlink trước — chỉ remote của nó học được mã.",
    "overwrite.exists":
      "Mã thiết bị {code} đã có rồi. Lưu lại sẽ ghi đè lên nó.",
    "chip.replaceExisting": "Ghi đè file đang có",
    // Setup, card 2
    "step.identify": "Nhận dạng nó",
    "label.manufacturer": "Hãng",
    "template.intro":
      "Bắt đầu từ một file thiết bị gần đúng thì đỡ việc hơn nhiều so với bắt " +
      "đầu từ số không. Nạp được bất kỳ mã đang có — các thiết lập và mọi mã " +
      "file đó đã giữ sẽ theo sang, chỉ còn những chỗ trống là phải ghi. Lưu " +
      "lại luôn ghi vào mã của riêng bạn, nên file gốc không bị chạm tới.",
    "placeholder.templateCode": "ví dụ 1000",
    "button.loadTemplate": "Nạp file thiết bị đó",
    "label.yourRecordings": "Bản ghi của bạn:",
    "title.download": "Tải {code}.json — giữ một bản trước khi cài lại",
    "aria.download": "Tải {code}.json",
    // What can it do?
    "step.capabilities": "Nó làm được gì?",
    "chip.reversible": "Đảo chiều được",
    "chip.oscillates": "Quay được",
    "chip.nightLight": "Có đèn ngủ",
    "switch.toggleNote":
      "Phần lớn remote có nút bật và nút tắt riêng. Một số — máy chiếu là hay " +
      "gặp nhất — chỉ có một nút nguồn mà mã của nó luân phiên; tích vào đây " +
      "thì panel sẽ ghi đúng cái nút đó.",
    "chip.toggleOnePower": "Một nút nguồn dùng chung cho bật và tắt",
    // Climate spec
    "step.temperaturesModes": "Nhiệt độ và các chế độ",
    "label.min": "Thấp nhất",
    "label.max": "Cao nhất",
    "label.step": "Bước nhảy",
    "label.unit": "Đơn vị",
    "label.operationModes": "Các chế độ hoạt động",
    "chip.separateOn": "Có mã bật nguồn riêng",
    "heading.whichModesIgnore": "Chế độ nào bỏ qua cái gì?",
    "climate.modeOptionsNote":
      "Phần lớn máy bỏ qua nhiệt độ ở <em>dry</em> và <em>fan only</em>, và " +
      "một số bỏ qua cả tốc độ quạt. Khai ở đây chính là khác biệt giữa bấm " +
      "remote một trăm lần và hai trăm lần — cùng một mã được ghi vào mọi chỗ " +
      "nó áp dụng.",
    "table.mode": "Chế độ",
    "table.respondsFan": "Theo tốc độ quạt",
    "table.respondsTemp": "Theo nhiệt độ",
    "word.yes": "có",
    "word.no": "không",
    // Presets
    "step.oneTouch": "Nút một chạm",
    "step.oneTouchLong": "Nút một chạm (Turbo, Eco, Sleep)",
    "preset.needModes":
      "Hãy chọn ít nhất một chế độ hoạt động và một tốc độ quạt trước — một " +
      "nút một chạm phải được ghi từ một trạng thái gọi được tên.",
    "preset.explain":
      "Trên phần lớn máy điều hoà, mấy nút này <strong>không</strong> gửi một " +
      "gói tin nhỏ kiểu &ldquo;bật turbo&rdquo;. Chúng gửi toàn bộ trạng thái " +
      "của máy &mdash; chế độ, tốc độ quạt và nhiệt độ &mdash; với một bit " +
      "được lật. Nghĩa là mã bạn ghi ở đây sẽ luôn kéo máy về đúng trạng thái " +
      "mà remote đang hiển thị lúc bạn bấm. Hãy chọn trạng thái đó một lần ở " +
      "dưới; panel sẽ đưa nó lên mọi màn hình ghi mã và viết nó vào file thiết " +
      "bị, để về sau không ai phải đoán.",
    "label.presetMode": "Ghi chúng từ chế độ này",
    "label.presetFan": "…tốc độ quạt này",
    "label.presetTemp": "…và nhiệt độ này",
    // Extra buttons
    "step.otherButtons": "Còn nút nào nữa không",
    "extras.explain":
      "Mọi thứ khác trên remote &mdash; nút menu, các mũi tên, các chữ số, nút " +
      "bật tắt đèn LED, một nguồn vào hay dùng. Chúng không gắn vào các điều " +
      "khiển thường của entity; chúng được gọi theo tên từ service " +
      "<code>hub_ir.send_command</code> và từ script. Tên ngắn, viết thường thì " +
      "dễ dùng nhất.",
    // The list editor
    "list.orderMatters": "— thứ tự có ý nghĩa",
    "list.empty": "Chưa có gì.",
    "list.use": "Dùng {items}",
    "list.add": "Thêm",
    "list.placeholder": "thêm {one}",
    "aria.moveUp": "Đưa {name} lên",
    "aria.moveDown": "Đưa {name} xuống",
    "aria.remove": "Xoá {name}",
    "list.models.label": "Các model",
    "list.models.one": "model",
    "list.fanModes.label": "Các tốc độ quạt",
    "list.fanModes.one": "tốc độ quạt",
    "list.fanModes.note": "Khớp với các mã bạn ghi, theo đúng thứ tự này.",
    "list.swingModes.label": "Các vị trí đảo gió",
    "list.swingModes.one": "vị trí đảo gió",
    "list.swingModes.note":
      "Để trống, trừ khi thiết lập đảo gió nằm trong gói tin của máy.",
    "list.speed.label": "Các tốc độ",
    "list.speed.one": "tốc độ",
    "list.speed.note": "Chậm nhất trước.",
    "list.brightness.label": "Các mức sáng",
    "list.brightness.one": "mức",
    "list.brightness.note": "Tối nhất trước, theo thang 1–255 của Home Assistant.",
    "list.colorTemperature.label": "Các nhiệt độ màu",
    "list.colorTemperature.one": "nhiệt độ",
    "list.colorTemperature.note": "Theo kelvin, ấm nhất trước.",
    "list.sources.label": "Nguồn vào và kênh",
    "list.sources.one": "nguồn vào",
    "list.presets.label": "Nút một chạm",
    "list.presets.one": "nút",
    "list.extraCommands.label": "Các nút khác",
    "list.extraCommands.one": "nút",
    "list.extraCommands.note":
      "Gọi được từ service hub_ir.send_command, theo tên.",
    "button.buildList": "Dựng danh sách mã cần ghi",
    // Capture
    "capture.heading": "Chĩa remote gốc vào Broadlink",
    "capture.setRemote": "Đặt remote về đúng cái này, rồi bấm gửi:",
    "capture.progress": "{done}/{total} · {group}",
    "capture.allDone": "Đã xong cả {total} mã",
    "capture.presetNote":
      "Mã này mang theo toàn bộ trạng thái của máy, nên hãy đặt remote về " +
      "<strong>{state}</strong> trước khi bấm.",
    "capture.clickSquare": "Bấm vào một ô để quay lại mã đó và ghi lại.",
    "cell.title": "{label} — bấm để quay lại mã này",
    "button.start": "Bắt đầu ghi",
    "button.justOne": "Chỉ mã này",
    "button.stop": "Dừng",
    "button.skip": "Bỏ qua",
    "button.testLast": "Thử mã vừa ghi",
    "chip.twoPacket": "Nút hai gói tin",
    "capture.toggleNote":
      "Một số remote luân phiên giữa hai gói tin cho cùng một nút — nút nguồn " +
      "của Samsung là ca hay gặp. Panel sẽ xin Broadlink cả hai và lưu thành " +
      "một cặp; tích hợp gửi lần lượt. Cứ để tắt, trừ khi mã đã ghi chỉ ăn " +
      "một lần bấm cách một.",
    "capture.listening":
      "Đang nghe… mỗi mã chờ tối đa 30 giây. Cứ bấm tiếp; panel tự chuyển sang " +
      "mã kế.",
    "button.backToSettings": "Trở lại phần cài đặt",
    "button.saveAs": "Lưu thành mã thiết bị {code}",
    "capture.skippedNote":
      "Đã bỏ qua {count} mã; chúng để trống và tích hợp từ chối gửi.",
    // Saved
    "saved.heading": "Đã lưu",
    "saved.writtenTo": "Đã ghi vào <code>{path}</code>.",
    "saved.worthKnowing": "Có mấy điểm nên biết:",
    "saved.noGaps": "Không thiếu mã nào.",
    "button.teachAnother": "Dạy một thiết bị khác",
    // Share
    "share.heading": "Gửi nó lên repo",
    "share.noExport":
      "File đã được ghi, nhưng không đọc lại được để xuất ra. Nó vẫn nằm trên " +
      "đĩa ở đường dẫn phía trên.",
    "share.intro":
      "Một file thiết bị chỉ giúp được người tiếp theo nếu nó ra khỏi máy này. " +
      "Hai việc, theo thứ tự:",
    "button.copyJson": "Copy JSON",
    "button.downloadFile": "Tải {filename}",
    "share.tooBig":
      "quá lớn cho bất kỳ URL issue nào, nên nó phải đi kèm dạng tệp đính kèm " +
      "hoặc dán vào.",
    "share.smallEnough": "đủ nhỏ để dán thẳng vào issue.",
    "link.prefilledIssue": "Mở issue đã điền sẵn",
    "share.issueNote":
      "Hãng, model, số lượng mã và phiên bản của bạn đã được điền sẵn; hãy bỏ " +
      "file bạn vừa copy hoặc vừa tải vào ô mà nó để trống cho bạn. " +
      "<strong>Đường link không mang theo mã nào cả</strong>, và không có gì " +
      "được tải lên cho tới khi bạn bấm nút trên GitHub.",
    "button.showRaw": "Xem JSON thô",
    "button.hideRaw": "Ẩn JSON thô",
    "share.copied": "Đã copy. Dán vào issue, hoặc đính kèm file đã tải.",
    // Create
    "create.heading": "Thêm nó vào Home Assistant",
    "create.sub": "Không YAML, không restart — entity hiện ra ngay.",
    "label.name": "Tên",
    "placeholder.myPlatform": "{platform} của tôi",
    "create.remoteNote":
      "Đã đặt sẵn đúng remote bạn dùng để ghi mã; chỉ đổi nếu thiết bị này nằm " +
      "trước một remote khác.",
    "button.create": "Tạo entity",
    "button.creating": "Đang tạo…",
    "create.yamlSummary": "Hoặc tự viết vào configuration.yaml",
    "create.yamlNote":
      "Chỉ nên làm nếu bạn giữ entity trong YAML. Nó cần restart, và hai cách " +
      "cấu hình cùng một thiết bị không biết gì về nhau — nên chọn một, đừng " +
      "cả hai.",
    // Created
    "created.already": "Đã có trong Home Assistant",
    "created.added": "Đã thêm vào Home Assistant",
    "created.existingText":
      "<code>{label}</code> đang dùng mã thiết bị {code}. Nó đã được nạp lại, " +
      "nên đang chạy trên file bạn vừa lưu.",
    "created.newText": "Đã tạo <code>{label}</code>. Không cần restart gì.",
    "button.show": "Xem nó",
    "link.manage": "Quản lý thiết bị HubIR",
    "created.renameNote":
      "Đổi tên, gán vào khu vực, hay trỏ nó sang một remote khác ở Cài đặt " +
      "&rarr; Thiết bị &amp; dịch vụ &rarr; HubIR.",
    // The remote picker
    "remote.notBroadlink": " — không phải remote Broadlink",
    // Messages
    "err.enterDeviceCode": "Nhập mã thiết bị trước đã.",
    "err.nothingToTest": "Chưa ghi được mã nào để thử.",
    "err.giveName": "Đặt tên cho nó trước đã.",
    "err.clipboard": "Clipboard bị từ chối. Hãy chọn đoạn text dưới đây và copy.",
    "err.generic": "Có gì đó không ổn.",
    "err.alreadyExists":
      "{message}. Hãy tích “Ghi đè file đang có”, hoặc chọn một mã thiết bị " +
      "khác ở phần cài đặt.",
    "ok.loadedTemplate":
      "Đã nạp mã thiết bị {code}: {kept} mã đã được ghi sẵn. Lưu lại sẽ ghi " +
      "vào {target}, không chạm tới file gốc.",
    "ok.sentCode": "Đã gửi mã của {label}.",
    "err.typeSomething": "Nhập gì đó trước đã.",
    "err.noSlash":
      "Tên không được chứa dấu gạch chéo — nó sẽ cắt key của mã làm hai.",
    "err.oneAtATime": "Thêm từng cái một; không cần dấu phẩy nữa.",
    "err.tooLong": "Giữ dưới {max} ký tự.",
    "err.numbersOnly": "Chỉ số — 2700, không phải “ấm”.",
    "err.positive": "Phải là một số dương.",
    "err.duplicate": "“{value}” đã có trong danh sách.",
    "err.duplicateNumber": "{value} đã có trong danh sách.",
    "unit.bytes": "byte",
    // Platform names
    "platform.climate": "Máy điều hoà",
    "platform.fan": "Quạt",
    "platform.light": "Đèn",
    "platform.media_player": "TV / đầu phát",
    "platform.switch": "Công tắc hoặc ổ cắm",
    // The capture labels and groups the server builds. Token by token: what is
    // not in here is a device-file key and stays as it is.
    "plan.Power": "Nguồn",
    "plan.Extras": "Thêm",
    "plan.Brightness": "Độ sáng",
    "plan.Colour": "Màu",
    "plan.Presets": "Nút một chạm",
    "plan.Extra buttons": "Các nút khác",
    "plan.Sources": "Nguồn vào",
    "plan.Volume": "Âm lượng",
    "plan.Channels": "Kênh",
    "plan.On": "Bật",
    "plan.Off": "Tắt",
    "plan.Oscillate": "Quay",
    "plan.Brighter": "Sáng hơn",
    "plan.Dimmer": "Tối hơn",
    "plan.Colder": "Lạnh hơn",
    "plan.Warmer": "Ấm hơn",
    "plan.Night light": "Đèn ngủ",
    "plan.Volume up": "Tăng âm lượng",
    "plan.Volume down": "Giảm âm lượng",
    "plan.Mute": "Tắt tiếng",
    "plan.Previous channel": "Kênh trước",
    "plan.Next channel": "Kênh sau",
    "plan.Toggle (a remote with one power button)":
      "Chuyển trạng thái (remote chỉ có một nút nguồn)",
    "plan.Extra": "Nút khác",
    "plan.Source": "Nguồn vào",
    "plan.Preset": "Một chạm",
    "plan.any fan": "mọi tốc độ quạt",
    "plan.presetHint": "{head} — đặt remote về {state} trước",
  },
};

/**
 * Return `key` in `lang`, or `fallback` when there is no translation for it.
 *
 * The fallback is the English text at the call site, so an untranslated string
 * degrades to English and never to a key.
 */
function tr(lang, key, fallback) {
  const table = STRINGS[lang];
  const found = table && table[key];
  return found === undefined ? fallback : found;
}

/** Fill {name} placeholders. Values are substituted verbatim, so escape first. */
function fill(template, values = {}) {
  return String(template).replace(/\{(\w+)\}/g, (whole, name) =>
    name in values ? String(values[name]) : whole
  );
}

/**
 * Translate a capture label or group the server built.
 *
 * The wire value stays English — it is protocol, and the panel compares against
 * it — so this only touches what is rendered. Labels are built by joining parts
 * with " · ", and the parts that are not fixed English words are mode names,
 * fan speeds and source names: keys in the device file. Translating token by
 * token leaves those alone, which is the point.
 */
function planText(lang, text) {
  const value = String(text ?? "");
  if (!STRINGS[lang]) return value;

  const tokens = (part) =>
    part
      .split(" · ")
      .map((token) => tr(lang, `plan.${token}`, token))
      .join(" · ");

  // A preset label carries a sentence, which has to come apart before the
  // " · " split reaches the state it names.
  const preset = value.match(/^(.*) — set the remote to (.*) first$/);
  if (preset) {
    return fill(tr(lang, "plan.presetHint", "{head} — set the remote to {state} first"), {
      head: tokens(preset[1]),
      state: tokens(preset[2]),
    });
  }
  return tokens(value);
}

const MAX_NAME_LENGTH = 64;

/**
 * Above this a device file is not going into a comment by hand.
 *
 * A three-mode air conditioner comes to about 23 kB, so in practice climate
 * files are always attachments and a switch or a fan can be pasted.
 */
const PASTEABLE_BYTES = 4000;

const STYLES = `
  :host { display: block; height: 100%; background: var(--primary-background-color); }
  .wrap { max-width: 60rem; margin: 0 auto; padding: 1rem 1rem 4rem; box-sizing: border-box; }
  h1 { font-size: 1.5rem; margin: 0; }
  h2 { font-size: 1.05rem; margin: 0 0 .75rem; }
  header {
    display: flex; align-items: center; gap: .75rem;
    padding: .75rem 1rem; background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff);
  }
  header svg { flex: 0 0 auto; border-radius: 6px; }
  .card {
    background: var(--card-background-color, #fff);
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.14));
    padding: 1rem; margin-bottom: 1rem; color: var(--primary-text-color);
  }
  label { display: block; font-size: .8rem; color: var(--secondary-text-color); margin-bottom: .2rem; }
  input, select {
    width: 100%; box-sizing: border-box; padding: .5rem;
    border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
    font: inherit;
  }
  .grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
  .row { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
  button {
    font: inherit; padding: .5rem .9rem; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--divider-color, #ccc);
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
  }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.danger { color: var(--error-color, #db4437); }
  button:disabled { opacity: .5; cursor: default; }
  .chips { display: flex; flex-wrap: wrap; gap: .4rem; }
  .chip {
    padding: .3rem .6rem; border-radius: 999px; cursor: pointer; font-size: .85rem;
    border: 1px solid var(--divider-color, #ccc); user-select: none;
  }
  .chip[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  .target { font-size: 1.6rem; font-weight: 500; margin: .25rem 0 .1rem; }
  .muted { color: var(--secondary-text-color); font-size: .85rem; }
  .bar { height: 8px; border-radius: 999px; background: var(--divider-color, #ddd); overflow: hidden; margin: .6rem 0; }
  .bar > div { height: 100%; background: var(--primary-color); transition: width .2s; }
  .cells { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .5rem; }
  .cell {
    width: .75rem; height: .75rem; border-radius: 2px; cursor: pointer;
    background: var(--divider-color, #ddd);
  }
  .cell.done { background: var(--success-color, #43a047); }
  .cell.skipped { background: var(--warning-color, #ffa600); }
  .cell.current { outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .status { padding: .6rem .8rem; border-radius: 6px; margin-top: .75rem; font-size: .9rem; }
  .status.error { background: rgba(219,68,55,.12); color: var(--error-color, #db4437); }
  .status.ok { background: rgba(67,160,71,.12); color: var(--success-color, #43a047); }
  pre {
    background: var(--secondary-background-color, #f3f3f3); color: var(--primary-text-color);
    padding: .75rem; border-radius: 6px; overflow-x: auto; font-size: .8rem;
  }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  td, th { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--divider-color, #eee); }
  ul { margin: .4rem 0; padding-left: 1.2rem; font-size: .85rem; }
  a { color: var(--primary-color); }
  summary { cursor: pointer; }
  .grid.wide { grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); }
  .list + .list { margin-top: 1rem; }
  ol.items { margin: .3rem 0 .5rem; padding-left: 1.7rem; font-size: .9rem; }
  ol.items li { padding: .1rem 0; }
  .item { display: flex; align-items: center; gap: .5rem; }
  .item-name { flex: 1 1 auto; overflow-wrap: anywhere; }
  .item-tools { display: flex; gap: .2rem; flex: 0 0 auto; }
  button.icon { padding: .15rem .45rem; line-height: 1.1; font-size: .85rem; }
  .empty { margin: .3rem 0 .5rem; font-style: italic; }
  .addrow { flex-wrap: nowrap; margin-top: .25rem; }
  .addrow input { flex: 1 1 auto; min-width: 6rem; }
  .addrow button { flex: 0 0 auto; }
  .presets { margin-top: .4rem; }
  .presets .chip { font-size: .8rem; padding: .2rem .5rem; }
  .listerr { margin-top: .4rem; }
  a.button-link {
    display: inline-block; text-decoration: none; font: inherit;
    padding: .5rem .9rem; border-radius: 6px;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
  }
  textarea {
    border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
  }
`;

class BroadlinkIrPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._ready = false;
    this._state = {
      step: "setup",
      platform: "climate",
      remote: "",
      remotes: [],
      nextCode: {},
      deviceCode: null,
      templateCode: "",
      customCodes: [],
      spec: structuredClone(DEFAULT_SPEC.climate),
      cells: [],
      codes: {},
      skipped: {},
      index: 0,
      running: false,
      status: null,
      saved: null,
      entityName: "",
      creating: false,
      created: null,
      // Per-list scratch space, none of which is ever sent to the server:
      // what is currently typed in each add box, the last thing refused and
      // why, and which input to put the caret back into after a re-render.
      draft: {},
      listError: {},
      focus: null,
      // A capture-session setting, deliberately not in spec so it can never
      // reach build_device_file.
      toggle: false,
      // Permission to replace a device file that already exists.
      overwrite: false,
      // The file just saved, ready to copy or download.
      export: null,
      showRaw: false,
      copied: false,
    };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._ready) {
      this._ready = true;
      this._load();
    }
  }

  set narrow(_narrow) {
    // Accepted so Home Assistant can set it; the layout is fluid anyway.
  }

  async _call(message) {
    return this._hass.connection.sendMessagePromise(message);
  }

  // -- language ------------------------------------------------------------

  /**
   * The language to render in, as a bare code.
   *
   * Home Assistant hands the panel the user's own language, not the server's,
   * so two people looking at the same instance each get their own. Regional
   * variants collapse to the base language: there is one Vietnamese here, and
   * `pt-BR` should find a `pt` table rather than nothing.
   */
  _lang() {
    const hass = this._hass;
    const raw =
      (hass && (hass.language || (hass.locale && hass.locale.language))) || "";
    return String(raw).toLowerCase().split("-")[0];
  }

  /** Look up one string, falling back to the English text passed in. */
  _t(key, fallback, values) {
    const text = tr(this._lang(), key, fallback);
    return values ? fill(text, values) : text;
  }

  /** Translate a capture label or group name the server built. */
  _plan(text) {
    return planText(this._lang(), text);
  }

  /** The name of a device type, as the user's language calls it. */
  _platformLabel(platform) {
    return this._t(`platform.${platform}`, PLATFORM_LABELS[platform]);
  }

  /** Turn a failed websocket call into a sentence. */
  _describe(err) {
    return describe(err, this._lang());
  }

  async _load() {
    try {
      const info = await this._call({ type: "hub_ir/info" });
      const learnable = info.remotes.filter((remote) => remote.can_learn);
      this._state.remotes = info.remotes;
      this._state.nextCode = info.next_code;
      this._state.remote = (learnable[0] || info.remotes[0] || {}).entity_id || "";
      this._state.deviceCode = info.next_code[this._state.platform];
    } catch (err) {
      this._state.status = { kind: "error", text: this._describe(err) };
    }
    await this._refreshCustomCodes();
    this._render();
  }

  /** List the recordings already made, so they are one click away to reopen. */
  async _refreshCustomCodes() {
    try {
      const { custom } = await this._call({
        type: "hub_ir/list",
        platform: this._state.platform,
      });
      this._state.customCodes = custom;
    } catch {
      this._state.customCodes = [];
    }
  }

  // -- state helpers -------------------------------------------------------

  _set(patch) {
    Object.assign(this._state, patch);
    this._render();
  }

  _setSpec(patch) {
    Object.assign(this._state.spec, patch);
    this._render();
  }

  _remaining() {
    const { cells, codes, skipped } = this._state;
    return cells.filter((cell) => !codes[cell.key] && !skipped[cell.key]);
  }

  _cellState(cell) {
    if (this._state.codes[cell.key]) return "done";
    if (this._state.skipped[cell.key]) return "skipped";
    return "";
  }

  // -- actions -------------------------------------------------------------

  async _buildPlan() {
    try {
      const { cells } = await this._call({
        type: "hub_ir/plan",
        platform: this._state.platform,
        spec: this._specForServer(),
      });
      const state = this._state;
      state.cells = cells;
      state.index = 0;
      this._advanceToFirstGap();
      this._set({ step: "capture", status: null });
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
    }
  }

  /**
   * Load an existing device file and carry on from what it already records.
   *
   * The server derives the spec and the codes, including which modes the file
   * says ignore fan speed or temperature, so re-learning a device only costs
   * the codes that are actually missing.
   */
  async _loadTemplate() {
    const code = Number(this._state.templateCode);
    if (!code) {
      this._set({
        status: {
          kind: "error",
          text: this._t("err.enterDeviceCode", "Enter a device code first."),
        },
      });
      return;
    }

    try {
      const result = await this._call({
        type: "hub_ir/get",
        platform: this._state.platform,
        device_code: code,
      });

      const spec = { ...structuredClone(DEFAULT_SPEC[this._state.platform]), ...result.spec };
      spec.models = [...(result.spec.supportedModels || [])];
      delete spec.supportedModels;

      const kept = Object.keys(result.codes).length;
      this._set({
        spec,
        codes: result.codes,
        skipped: {},
        cells: [],
        index: 0,
        status: {
          kind: "ok",
          text: this._t(
            "ok.loadedTemplate",
            "Loaded device code {code}: {kept} code(s) already recorded. " +
              "Saving will write to {target}, leaving the original alone.",
            { code, kept, target: this._state.deviceCode }
          ),
        },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
    }
  }

  _advanceToFirstGap() {
    const { cells } = this._state;
    let index = 0;
    while (index < cells.length && this._cellState(cells[index])) index += 1;
    this._state.index = index;
  }

  _specForServer() {
    const spec = structuredClone(this._state.spec);
    spec.supportedModels = (spec.models || [])
      .map((entry) => String(entry).trim())
      .filter(Boolean);
    delete spec.models;

    if (this._state.platform === "climate") {
      // A preset has to be captured from a state that can be named, so drop
      // them entirely if the lists they refer to have been emptied since.
      if (!(spec.operationModes || []).length || !(spec.fanModes || []).length) {
        spec.presets = [];
      }
      // Send the resolved baseline rather than whatever was last clicked, so
      // the capture labels and the saved file cannot disagree.
      spec.presetBaseline = spec.presets.length ? this._presetBaseline() : {};
    }

    return spec;
  }

  async _learnCurrent(continuous) {
    const state = this._state;
    if (state.running) return;

    const cell = state.cells[state.index];
    if (!cell) return;

    this._set({ running: true, status: null });

    while (this._state.running) {
      const target = this._state.cells[this._state.index];
      if (!target) break;

      try {
        const { code } = await this._call({
          type: "hub_ir/learn",
          remote_entity_id: this._state.remote,
          toggle: this._state.toggle,
        });
        this._state.codes[target.key] = code;
        delete this._state.skipped[target.key];
        this._advance();
      } catch (err) {
        this._set({ running: false, status: { kind: "error", text: this._describe(err) } });
        return;
      }

      if (!continuous) break;
      if (!this._state.cells[this._state.index]) break;
      this._render();
    }

    this._set({ running: false });
  }

  _advance() {
    const { cells, index } = this._state;
    let next = index + 1;
    while (next < cells.length && this._cellState(cells[next])) next += 1;
    this._state.index = next < cells.length ? next : cells.length;
  }

  _skip() {
    const cell = this._state.cells[this._state.index];
    if (!cell) return;
    this._state.skipped[cell.key] = true;
    delete this._state.codes[cell.key];
    this._advance();
    this._render();
  }

  /**
   * Return the cell the buttons act on: the one being captured if it already
   * holds a code, otherwise the last one that does. Without the fallback there
   * is nothing to test right after a capture, because the panel has moved on.
   */
  _testableCell() {
    const { cells, codes, index } = this._state;
    if (cells[index] && codes[cells[index].key]) return cells[index];
    for (let i = Math.min(index, cells.length) - 1; i >= 0; i -= 1) {
      if (codes[cells[i].key]) return cells[i];
    }
    return null;
  }

  async _test() {
    const cell = this._testableCell();
    const code = cell && this._state.codes[cell.key];
    if (!code) {
      this._set({
        status: {
          kind: "error",
          text: this._t("err.nothingToTest", "Nothing captured to test yet."),
        },
      });
      return;
    }
    try {
      await this._call({
        type: "hub_ir/send",
        remote_entity_id: this._state.remote,
        code,
      });
      this._set({
        status: {
          kind: "ok",
          text: this._t("ok.sentCode", "Sent the code for {label}.", {
            label: this._plan(cell.label),
          }),
        },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
    }
  }

  async _save() {
    try {
      const result = await this._call({
        type: "hub_ir/save",
        platform: this._state.platform,
        device_code: this._state.deviceCode,
        spec: this._specForServer(),
        codes: this._state.codes,
        overwrite: this._state.overwrite,
      });

      // Fetched now rather than when Copy is pressed: the clipboard API wants to
      // run inside the click that asked for it, and a websocket round trip in
      // that handler risks losing the user activation. A failure here is not
      // fatal — the file is written either way.
      let exported = null;
      try {
        exported = await this._export(result.device_code);
      } catch {
        exported = null;
      }

      this._set({
        step: "saved",
        saved: result,
        export: exported,
        showRaw: false,
        copied: false,
        status: null,
        entityName: defaultName(
          this._state.spec,
          this._state.platform,
          this._lang()
        ),
        creating: false,
        // Clearing this matters: saving a second file after a create would
        // otherwise show the first entity's success over the new file.
        created: null,
      });
    } catch (err) {
      const text =
        err && err.code === "already_exists"
          ? this._t(
              "err.alreadyExists",
              "{message}. Tick “Replace the existing file”, or pick another " +
                "device code on the settings step.",
              { message: this._describe(err) }
            )
          : this._describe(err);
      this._set({ status: { kind: "error", text } });
    }
  }

  /** True when saving now would replace one of the user's own recordings. */
  _wouldOverwrite() {
    return this._state.customCodes.includes(Number(this._state.deviceCode));
  }

  /** Warn, and offer permission, when the chosen code is already taken. */
  _overwriteWarning() {
    if (!this._wouldOverwrite()) return "";
    return `<div class="status error">${this._t(
      "overwrite.exists",
      "Device code {code} already exists. Saving replaces it.",
      { code: esc(this._state.deviceCode) }
    )}</div>
      <div class="row" style="margin-top:.5rem">
        ${chip(
          "overwrite",
          this._t("chip.replaceExisting", "Replace the existing file"),
          this._state.overwrite
        )}
      </div>`;
  }

  /** Re-ask which code is free; the answer goes stale as soon as one is saved. */
  async _refreshNextCode() {
    try {
      const info = await this._call({ type: "hub_ir/info" });
      this._state.nextCode = info.next_code;
    } catch {
      // Keep the previous guess rather than losing the screen over it.
    }
  }

  /** Turn the device file just saved into a live entity, with no restart. */
  async _create() {
    const s = this._state;
    const name = String(s.entityName || "").trim();
    if (!name) {
      this._set({
        status: { kind: "error", text: this._t("err.giveName", "Give it a name first.") },
      });
      return;
    }

    this._set({ creating: true, status: null });
    try {
      const created = await this._call({
        type: "hub_ir/create_entity",
        platform: s.platform,
        device_code: s.saved.device_code,
        controller_data: s.remote,
        name,
      });
      this._set({ creating: false, created, status: null });
    } catch (err) {
      this._set({ creating: false, status: { kind: "error", text: this._describe(err) } });
    }
  }

  // -- rendering -----------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLES}</style>
      <header>${LOGO}<h1>HubIR</h1></header>
      <div class="wrap">${this._body()}</div>`;
    this._bind();

    // The add boxes are the only inputs that must survive a deliberate
    // re-render: Enter clears one and the user carries on typing into it.
    const focus = this._state.focus;
    if (focus) {
      this._state.focus = null;
      const node = root.getElementById(focus);
      if (node) {
        node.focus();
        if (node.setSelectionRange) {
          node.setSelectionRange(node.value.length, node.value.length);
        }
      }
    }
  }

  _body() {
    if (this._state.step === "setup") return this._setupView();
    if (this._state.step === "capture") return this._captureView();
    return this._savedView();
  }

  _setupView() {
    const s = this._state;
    this._cardNumber = 0;
    const learnable = s.remotes.filter((r) => r.can_learn);

    return `
      <div class="card">
        <h2>${this._step(this._t("step.teaching", "What are you teaching?"))}</h2>
        <div class="grid">
          <div>
            <label for="platform">${this._t("label.deviceType", "Device type")}</label>
            <select id="platform">
              ${Object.keys(PLATFORM_LABELS)
                .map(
                  (value) =>
                    `<option value="${value}"${
                      value === s.platform ? " selected" : ""
                    }>${esc(this._platformLabel(value))}</option>`
                )
                .join("")}
            </select>
          </div>
          <div>
            <label for="remote">${this._t("label.remote", "Broadlink remote")}</label>
            ${this._remoteSelect()}
          </div>
          <div>
            <label for="device_code">${this._t(
              "label.newDeviceCode",
              "New device code"
            )}</label>
            <input id="device_code" type="number" value="${s.deviceCode ?? ""}" />
          </div>
        </div>
        ${
          learnable.length
            ? ""
            : `<div class="status error">${this._t(
                "error.noRemote",
                "No Broadlink remote found. Set up the Broadlink integration " +
                  "first — only its remotes can learn codes."
              )}</div>`
        }
        ${this._overwriteWarning()}
      </div>

      <div class="card">
        <h2>${this._step(this._t("step.identify", "Identify it"))}</h2>
        <div class="grid">
          <div>
            <label for="manufacturer">${this._t(
              "label.manufacturer",
              "Manufacturer"
            )}</label>
            <input id="manufacturer" value="${esc(s.spec.manufacturer)}" placeholder="Daikin" />
          </div>
          <div>${this._listEditor("models")}</div>
        </div>

        <p class="muted" style="margin-top:1rem">
          ${this._t(
            "template.intro",
            "Starting from a device file that is nearly right is much less work " +
              "than starting from nothing. Any existing code can be loaded — the " +
              "settings and every code it already holds come with it, and only " +
              "the gaps are left to capture. Saving always writes to your own " +
              "code, so the original is untouched."
          )}
        </p>
        <div class="row">
          <input id="template_code" type="number"
                 placeholder="${esc(this._t("placeholder.templateCode", "e.g. 1000"))}"
                 value="${esc(s.templateCode)}" style="max-width:10rem" />
          <button id="load_template">${this._t(
            "button.loadTemplate",
            "Load that device file"
          )}</button>
        </div>
        ${
          s.customCodes.length
            ? `<div class="row" style="margin-top:.5rem">
                 <span class="muted">${this._t(
                   "label.yourRecordings",
                   "Your recordings:"
                 )}</span>
                 ${s.customCodes
                   .map(
                     (code) =>
                       `<span class="chip" data-reopen="${code}" role="button">${code}</span>
                        <button type="button" class="icon" data-download="${code}"
                          title="${esc(
                            this._t(
                              "title.download",
                              "Download {code}.json — keep a copy before you reinstall",
                              { code }
                            )
                          )}"
                          aria-label="${esc(
                            this._t("aria.download", "Download {code}.json", { code })
                          )}">&#11015;</button>`
                   )
                   .join("")}
               </div>`
            : ""
        }
      </div>

      ${this._specView()}
      ${this._presetCard()}
      ${this._extrasCard()}

      <div class="row">
        <button class="primary" id="plan" ${learnable.length ? "" : "disabled"}>
          ${this._t("button.buildList", "Build the list of codes")}
        </button>
      </div>
      ${this._statusView()}
    `;
  }

  /**
   * Number the setup cards in the order they are rendered.
   *
   * Rendering is one synchronous pass, so a counter is safe, and it keeps the
   * headings from drifting out of step with PANEL.md the next time a card is
   * added or made conditional.
   */
  _step(title) {
    this._cardNumber = (this._cardNumber || 0) + 1;
    return `${this._cardNumber} · ${esc(title)}`;
  }

  /**
   * The one-touch buttons: Turbo, Eco, Sleep. Climate only.
   *
   * A standing explanation, not just a note on each capture screen. The screen
   * can say *what* state to dial in; it cannot say why the panel is asking, and
   * somebody who does not understand why will leave the remote on whatever it
   * happens to show and record a Turbo code that silently drags the unit to
   * 30°C every time it fires.
   */
  _presetCard() {
    const s = this._state;
    if (s.platform !== "climate") return "";

    const modes = s.spec.operationModes || [];
    const fanModes = s.spec.fanModes || [];

    if (!modes.length || !fanModes.length) {
      return `<div class="card">
        <h2>${this._step(this._t("step.oneTouch", "One-touch buttons"))}</h2>
        <div class="status error">
          ${this._t(
            "preset.needModes",
            "Choose at least one operation mode and one fan speed first — a " +
              "one-touch button has to be recorded from a state you can name."
          )}
        </div>
      </div>`;
    }

    const baseline = this._presetBaseline();
    const option = (value, selected) =>
      `<option value="${esc(value)}"${
        String(value) === String(selected) ? " selected" : ""
      }>${esc(value)}</option>`;

    return `<div class="card">
      <h2>${this._step(
        this._t("step.oneTouchLong", "One-touch buttons (Turbo, Eco, Sleep)")
      )}</h2>
      <p class="muted">
        ${this._t(
          "preset.explain",
          "On most air conditioners these do <strong>not</strong> send a small " +
            "&ldquo;turbo on&rdquo; packet. They send the unit&rsquo;s whole " +
            "state &mdash; mode, fan speed and temperature &mdash; with one " +
            "extra bit flipped. So the code you record here will always put the " +
            "unit back into whichever state the remote was showing when you " +
            "pressed it. Pick that state once, below; the panel puts it on every " +
            "capture screen and writes it into the device file, so nobody has to " +
            "guess later."
        )}
      </p>
      <div class="grid" style="margin-top:.75rem">
        <div>
          <label for="presetBaseMode">${this._t(
            "label.presetMode",
            "Record them from this mode"
          )}</label>
          <select id="presetBaseMode">
            ${modes.map((mode) => option(mode, baseline.operationMode)).join("")}
          </select>
        </div>
        <div>
          <label for="presetBaseFanMode">${this._t(
            "label.presetFan",
            "…this fan speed"
          )}</label>
          <select id="presetBaseFanMode">
            ${fanModes.map((fan) => option(fan, baseline.fanMode)).join("")}
          </select>
        </div>
        <div>
          <label for="presetBaseTemperature">${this._t(
            "label.presetTemp",
            "…and this temperature"
          )}</label>
          <input id="presetBaseTemperature" type="number" step="${s.spec.precision}"
            min="${s.spec.minTemperature}" max="${s.spec.maxTemperature}"
            value="${baseline.temperature}" />
        </div>
      </div>
      <div style="margin-top:1rem">${this._listEditor("presets")}</div>
    </div>`;
  }

  /** The base state presets are captured from, filled in the way the server would. */
  _presetBaseline() {
    const spec = this._state.spec;
    const declared = spec.presetBaseline || {};
    const modes = spec.operationModes || [];
    const fanModes = spec.fanModes || [];

    const steps = [];
    for (
      let value = spec.minTemperature;
      value <= spec.maxTemperature;
      value = Math.round((value + spec.precision) * 100) / 100
    ) {
      steps.push(value);
    }

    return {
      operationMode: modes.includes(declared.operationMode)
        ? declared.operationMode
        : modes[0],
      fanMode: fanModes.includes(declared.fanMode) ? declared.fanMode : fanModes[0],
      // The middle step, matching the server: nobody dials an air conditioner
      // down to 16°C to record a preset.
      temperature: steps.includes(declared.temperature)
        ? declared.temperature
        : steps[Math.floor(steps.length / 2)],
    };
  }

  /** Describe the preset base state the way the capture labels spell it. */
  _presetBaseLabel() {
    const baseline = this._presetBaseline();
    const unit = this._state.spec.temperatureUnit === "F" ? "°F" : "°C";
    return [
      baseline.operationMode,
      baseline.fanMode,
      `${baseline.temperature}${unit}`,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  /** Free-form buttons, for every platform. */
  _extrasCard() {
    const s = this._state;
    return `<div class="card">
      <h2>${this._step(this._t("step.otherButtons", "Any other buttons"))}</h2>
      <p class="muted">
        ${this._t(
          "extras.explain",
          "Anything else on the remote &mdash; a menu key, the arrows, the " +
            "digits, an LED toggle, a favourite input. These are not wired to " +
            "the entity&rsquo;s normal controls; they are reachable by name from " +
            "the <code>hub_ir.send_command</code> service and from scripts. " +
            "Short lower-case names travel best."
        )}
      </p>
      <div style="margin-top:.75rem">
        ${this._listEditor("extraCommands", {
          presets: EXTRA_PRESETS[s.platform] || [],
        })}
      </div>
    </div>`;
  }

  /**
   * Return the description of a list field, with any per-call overrides.
   *
   * The label, the singular noun and the note are prose, so they come from the
   * translation table when there is one; LIST_FIELDS holds the English and
   * everything else — the order flag, the presets, the fill — which is not
   * language at all.
   */
  _listConfig(field, options = {}) {
    const config = { presets: [], ...LIST_FIELDS[field], ...options };
    config.label = this._t(`list.${field}.label`, config.label);
    config.one = this._t(`list.${field}.one`, config.one);
    if (config.note) config.note = this._t(`list.${field}.note`, config.note);
    return config;
  }

  /**
   * Render one editable, ordered list.
   *
   * The add box's contents live in state.draft so a keystroke never re-renders;
   * every other action does re-render, because a button press has no caret to
   * lose.
   */
  _listEditor(field, options = {}) {
    const config = this._listConfig(field, options);
    const list = this._state.spec[field] || [];
    const error = this._state.listError[field];
    const addId = `list_add_${field}`;

    // An <ol> rather than a <ul>: the number the user sees is the position the
    // server will match against, so it is content, not decoration.
    const items = list.length
      ? `<ol class="items">${list
          .map((entry, index) => {
            const name = esc(entry);
            return `<li><div class="item">
              <span class="item-name">${name}</span>
              <span class="item-tools">
                <button type="button" class="icon" data-list="${field}" data-act="up"
                  data-index="${index}"
                  aria-label="${this._t("aria.moveUp", "Move {name} up", { name })}"
                  ${index === 0 ? "disabled" : ""}>&#8593;</button>
                <button type="button" class="icon" data-list="${field}" data-act="down"
                  data-index="${index}"
                  aria-label="${this._t("aria.moveDown", "Move {name} down", { name })}"
                  ${index === list.length - 1 ? "disabled" : ""}>&#8595;</button>
                <button type="button" class="icon danger" data-list="${field}"
                  data-act="remove" data-index="${index}"
                  aria-label="${this._t("aria.remove", "Remove {name}", {
                    name,
                  })}">&#10005;</button>
              </span>
            </div></li>`;
          })
          .join("")}</ol>`
      : `<p class="muted empty">${this._t("list.empty", "Nothing yet.")}</p>`;

    // Only offered while the list is empty, and only when there is a
    // conventional answer: one click instead of inventing four names.
    const fillChip =
      !list.length && config.fill
        ? `<span class="chip" role="button" data-list="${field}" data-act="fill"
             >${this._t("list.use", "Use {items}", {
               items: config.fill.join(" · "),
             })}</span>`
        : "";

    // A suggestion already in the list is not rendered at all. Hiding beats
    // disabling: the row stays short and there is no dead click to explain.
    const suggestions = config.presets
      .filter((entry) => !list.some((existing) => String(existing) === String(entry)))
      .map(
        (entry) =>
          `<span class="chip" role="button" data-list="${field}" data-act="preset"
             data-value="${esc(entry)}">+ ${esc(entry)}</span>`
      )
      .join("");

    return `<div class="list">
      <label for="${addId}">${esc(config.label)}${
        config.ordered
          ? ` <span class="muted">${this._t(
              "list.orderMatters",
              "— order matters"
            )}</span>`
          : ""
      }</label>
      ${config.note ? `<p class="muted" style="margin:.1rem 0 .3rem">${esc(config.note)}</p>` : ""}
      ${items}
      <div class="row addrow">
        <input id="${addId}" type="text" autocomplete="off"
          ${config.numeric ? 'inputmode="numeric"' : ""}
          placeholder="${esc(
            this._t("list.placeholder", "add a {one}", { one: config.one })
          )}"
          value="${esc(this._state.draft[field] || "")}" />
        <button type="button" data-list="${field}" data-act="add">${this._t(
          "list.add",
          "Add"
        )}</button>
      </div>
      ${
        fillChip || suggestions
          ? `<div class="chips presets">${fillChip}${suggestions}</div>`
          : ""
      }
      ${error ? `<div class="status error listerr">${esc(error)}</div>` : ""}
    </div>`;
  }

  /** Apply an add / remove / reorder / suggestion action to a spec list. */
  _listAction(field, act, data = {}) {
    const config = this._listConfig(field, {
      presets: EXTRA_PRESETS[this._state.platform] || [],
    });
    const list = [...(this._state.spec[field] || [])];
    const index = Number(data.index);

    if (act === "remove") {
      list.splice(index, 1);
    } else if (act === "up" && index > 0) {
      [list[index - 1], list[index]] = [list[index], list[index - 1]];
    } else if (act === "down" && index < list.length - 1) {
      [list[index], list[index + 1]] = [list[index + 1], list[index]];
    } else if (act === "fill") {
      if (list.length || !config.fill) return;
      list.push(...config.fill);
    } else if (act === "add" || act === "preset") {
      const raw = act === "preset" ? data.value : this._state.draft[field];
      const outcome = listValue(raw, config, list, this._lang());
      if (outcome.error) {
        this._state.listError[field] = outcome.error;
        this._state.focus = `list_add_${field}`;
        this._render();
        return;
      }
      list.push(outcome.value);
      this._state.draft[field] = "";
    }

    delete this._state.listError[field];
    // One rule for every action, including a suggestion chip: the caret goes
    // back to the add box, so building a list of six feels like typing a list.
    this._state.focus = `list_add_${field}`;
    this._setSpec({ [field]: list });
  }

  _specView() {
    const s = this._state;
    if (s.platform === "climate") return this._climateSpecView();

    const heading = this._t("step.capabilities", "What can it do?");

    if (s.platform === "fan") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        ${this._listEditor("speed")}
        <div class="row" style="margin-top:.75rem">
          ${chip(
            "hasDirection",
            this._t("chip.reversible", "Reversible"),
            s.spec.hasDirection
          )}
          ${chip(
            "hasOscillate",
            this._t("chip.oscillates", "Oscillates"),
            s.spec.hasOscillate
          )}
        </div>
      </div>`;
    }

    if (s.platform === "light") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        <div class="grid wide">
          <div>${this._listEditor("brightness")}</div>
          <div>${this._listEditor("colorTemperature")}</div>
        </div>
        <div class="row" style="margin-top:.75rem">
          ${chip(
            "hasNight",
            this._t("chip.nightLight", "Has a night light"),
            s.spec.hasNight
          )}
        </div>
      </div>`;
    }

    if (s.platform === "switch") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        <p class="muted">
          ${this._t(
            "switch.toggleNote",
            "Most remotes have separate on and off keys. Some — projectors " +
              "especially — have a single power key whose code just alternates; " +
              "tick this and the panel records that one instead."
          )}
        </p>
        <div class="row">
          ${chip(
            "hasToggle",
            this._t("chip.toggleOnePower", "One power button that toggles"),
            s.spec.hasToggle
          )}
        </div>
      </div>`;
    }

    return `<div class="card">
      <h2>${this._step(heading)}</h2>
      <div class="chips">
        ${["on", "off", "volumeUp", "volumeDown", "mute", "previousChannel", "nextChannel"]
          .map((name) =>
            chip(`button:${name}`, name, s.spec.buttons.includes(name))
          )
          .join("")}
      </div>
      <div style="margin-top:.75rem">${this._listEditor("sources")}</div>
    </div>`;
  }

  _climateSpecView() {
    const spec = this._state.spec;
    const modes = spec.operationModes;

    return `<div class="card">
      <h2>${this._step(
        this._t("step.temperaturesModes", "Temperatures and modes")
      )}</h2>
      <div class="grid">
        <div><label for="minTemperature">${this._t("label.min", "Minimum")}</label>
          <input id="minTemperature" type="number" step="any" value="${spec.minTemperature}" /></div>
        <div><label for="maxTemperature">${this._t("label.max", "Maximum")}</label>
          <input id="maxTemperature" type="number" step="any" value="${spec.maxTemperature}" /></div>
        <div><label for="precision">${this._t("label.step", "Step")}</label>
          <input id="precision" type="number" step="any" value="${spec.precision}" /></div>
        <div><label for="temperatureUnit">${this._t("label.unit", "Unit")}</label>
          <select id="temperatureUnit">
            <option value="C"${spec.temperatureUnit === "C" ? " selected" : ""}>Celsius</option>
            <option value="F"${spec.temperatureUnit === "F" ? " selected" : ""}>Fahrenheit</option>
          </select></div>
      </div>

      <div style="margin-top:1rem">
        <label>${this._t("label.operationModes", "Operation modes")}</label>
        <div class="chips">
          ${CLIMATE_MODES.map((mode) =>
            chip(`mode:${mode}`, mode, modes.includes(mode))
          ).join("")}
        </div>
      </div>

      <div class="grid wide" style="margin-top:1rem">
        <div>${this._listEditor("fanModes")}</div>
        <div>${this._listEditor("swingModes")}</div>
      </div>

      <div class="row" style="margin-top:.75rem">
        ${chip(
          "hasOnCommand",
          this._t("chip.separateOn", "Separate power-on code"),
          spec.hasOnCommand
        )}
      </div>

      <h2 style="margin-top:1.5rem">${this._t(
        "heading.whichModesIgnore",
        "Which modes ignore what?"
      )}</h2>
      <p class="muted">
        ${this._t(
          "climate.modeOptionsNote",
          "Most units ignore the temperature in <em>dry</em> and <em>fan only" +
            "</em>, and some ignore the fan speed too. Saying so here is the " +
            "difference between pressing the remote a hundred times and a couple " +
            "of hundred — the same code is written everywhere it applies."
        )}
      </p>
      <table>
        <tr><th>${this._t("table.mode", "Mode")}</th><th>${this._t(
          "table.respondsFan",
          "Responds to fan speed"
        )}</th><th>${this._t(
          "table.respondsTemp",
          "Responds to temperature"
        )}</th></tr>
        ${modes
          .map((mode) => {
            const options = spec.modeOptions[mode] || {};
            const fan = options.usesFan !== false;
            const temp = options.usesTemperature !== false;
            const yes = this._t("word.yes", "yes");
            const no = this._t("word.no", "no");
            return `<tr>
              <td>${esc(mode)}</td>
              <td>${chip(`usesFan:${mode}`, fan ? yes : no, fan)}</td>
              <td>${chip(`usesTemperature:${mode}`, temp ? yes : no, temp)}</td>
            </tr>`;
          })
          .join("")}
      </table>
    </div>`;
  }

  _captureView() {
    const s = this._state;
    const total = s.cells.length;
    const done = Object.keys(s.codes).length;
    const skipped = Object.keys(s.skipped).length;
    const current = s.cells[s.index];
    const percent = total ? Math.round(((done + skipped) / total) * 100) : 0;

    return `
      <div class="card">
        <h2>${this._t(
          "capture.heading",
          "Point the original remote at the Broadlink"
        )}</h2>
        ${
          current
            ? `<p class="muted">${this._t(
                "capture.setRemote",
                "Set the remote to this, then press send:"
              )}</p>
               <div class="target">${esc(this._plan(current.label))}</div>
               <p class="muted">${this._t(
                 "capture.progress",
                 "{done} of {total} · {group}",
                 {
                   done: done + skipped,
                   total,
                   group: esc(this._plan(current.group)),
                 }
               )}</p>`
            : `<div class="target">${this._t(
                "capture.allDone",
                "All {total} codes accounted for",
                { total }
              )}</div>`
        }
        ${
          current && current.group === "Presets"
            ? `<div class="status ok">${this._t(
                "capture.presetNote",
                "This one carries the unit's whole state, so set the remote back " +
                  "to <strong>{state}</strong> before you press it.",
                { state: esc(this._presetBaseLabel()) }
              )}</div>`
            : ""
        }
        <div class="bar"><div style="width:${percent}%"></div></div>
        <div class="cells">
          ${s.cells
            .map(
              (cell, i) =>
                `<div class="cell ${this._cellState(cell)}${
                  i === s.index ? " current" : ""
                }" data-index="${i}" role="button" tabindex="0"
                  title="${this._t(
                    "cell.title",
                    "{label} — click to go back to it",
                    { label: esc(this._plan(cell.label)) }
                  )}"></div>`
            )
            .join("")}
        </div>
        <p class="muted">${this._t(
          "capture.clickSquare",
          "Click a square to return to that code and capture it again."
        )}</p>

        <div class="row" style="margin-top:1rem">
          <button class="primary" id="run" ${
            s.running || !current ? "disabled" : ""
          }>${this._t("button.start", "Start capturing")}</button>
          <button id="one" ${s.running || !current ? "disabled" : ""}>${this._t(
            "button.justOne",
            "Just this one"
          )}</button>
          <button id="stop" ${s.running ? "" : "disabled"}>${this._t(
            "button.stop",
            "Stop"
          )}</button>
          <button id="skip" ${s.running || !current ? "disabled" : ""}>${this._t(
            "button.skip",
            "Skip"
          )}</button>
          <button id="test" ${s.running ? "disabled" : ""}>${this._t(
            "button.testLast",
            "Test last code"
          )}</button>
        </div>
        <div class="row" style="margin-top:.6rem">
          ${chip("toggle", this._t("chip.twoPacket", "Two-packet button"), s.toggle)}
        </div>
        ${
          s.toggle
            ? `<p class="muted" style="margin-top:.4rem">
                 ${this._t(
                   "capture.toggleNote",
                   "Some remotes alternate between two packets for the same " +
                     "button — a Samsung power key is the usual one. The panel " +
                     "asks the Broadlink for both and stores them as a pair; the " +
                     "integration sends them in turn. Leave this off unless a " +
                     "captured code only works every other press."
                 )}
               </p>`
            : ""
        }
        ${
          s.running
            ? `<p class="muted" style="margin-top:.75rem">
                 ${this._t(
                   "capture.listening",
                   "Listening… each code times out after 30 seconds. Keep " +
                     "pressing; the panel moves on by itself."
                 )}
               </p>`
            : ""
        }
        ${this._statusView()}
      </div>

      ${this._overwriteWarning()}
      <div class="row">
        <button id="back">${this._t(
          "button.backToSettings",
          "Back to the settings"
        )}</button>
        <button class="primary" id="save" ${
          done && !(this._wouldOverwrite() && !s.overwrite) ? "" : "disabled"
        }>
          ${this._t("button.saveAs", "Save as device code {code}", {
            code: s.deviceCode,
          })}
        </button>
      </div>
      ${
        skipped
          ? `<p class="muted">${this._t(
              "capture.skippedNote",
              "{count} skipped; those stay empty and the integration refuses to " +
                "send them.",
              { count: skipped }
            )}</p>`
          : ""
      }
    `;
  }

  _savedView() {
    const s = this._state;

    return `<div class="card">
      <h2>${this._t("saved.heading", "Saved")}</h2>
      <p class="muted">${this._t("saved.writtenTo", "Written to <code>{path}</code>.", {
        path: esc(s.saved.path),
      })}</p>
      ${
        s.saved.warnings && s.saved.warnings.length
          ? `<div class="status error"><strong>${this._t(
              "saved.worthKnowing",
              "Worth knowing:"
            )}</strong>
             <ul>${s.saved.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`
          : `<div class="status ok">${this._t("saved.noGaps", "No gaps found.")}</div>`
      }
    </div>

    ${s.created ? this._createdView() : this._createView()}
    ${this._shareView()}

    <div class="row"><button id="restart">${this._t(
      "button.teachAnother",
      "Teach another device"
    )}</button></div>`;
  }

  /**
   * Offer to send the recording upstream.
   *
   * A device file only helps the next person if it can leave this machine, and
   * this panel used to show a filesystem path and stop there. What decides
   * whether a fork like this is worth using is how many devices it covers, and
   * that only grows if contributing is easier than not bothering.
   */
  _shareView() {
    const x = this._state.export;

    const heading = this._t("share.heading", "Send it upstream");

    if (!x) {
      return `<div class="card">
        <h2>${heading}</h2>
        <p class="muted">
          ${this._t(
            "share.noExport",
            "The file was written, but it could not be read back for export. It " +
              "is still on disk at the path above."
          )}
        </p>
      </div>`;
    }

    return `<div class="card">
      <h2>${heading}</h2>
      <p class="muted">
        ${this._t(
          "share.intro",
          "A device file only helps the next person if it leaves this machine. " +
            "Two things, in this order:"
        )}
      </p>
      <div class="row" style="margin-top:.75rem">
        <button id="copy_json">${this._t("button.copyJson", "Copy JSON")}</button>
        <button id="download_json">${this._t(
          "button.downloadFile",
          "Download {filename}",
          { filename: esc(x.filename) }
        )}</button>
      </div>
      <p class="muted" style="margin-top:.4rem">
        ${formatBytes(x.bytes, this._lang())} — ${
          x.bytes > PASTEABLE_BYTES
            ? this._t(
                "share.tooBig",
                "far too big for any issue URL, so it has to travel as an " +
                  "attachment or a paste."
              )
            : this._t(
                "share.smallEnough",
                "small enough to paste straight into the issue."
              )
        }
      </p>
      <div class="row" style="margin-top:.75rem">
        <a class="button-link" href="${esc(x.issue_url)}" target="_blank"
          rel="noopener">${this._t(
            "link.prefilledIssue",
            "Open a pre-filled issue"
          )}</a>
      </div>
      <p class="muted" style="margin-top:.4rem">
        ${this._t(
          "share.issueNote",
          "The make, the models, the code count and your versions are filled in " +
            "already; drop the file you just copied or downloaded into the box it " +
            "leaves for you. <strong>The link carries no codes at all</strong>, " +
            "and nothing is uploaded until you press the button on GitHub."
        )}
      </p>
      <div class="row" style="margin-top:.75rem">
        <button id="show_raw">${
          this._state.showRaw
            ? this._t("button.hideRaw", "Hide the raw JSON")
            : this._t("button.showRaw", "Show the raw JSON")
        }</button>
      </div>
      ${
        this._state.showRaw
          ? `<textarea id="raw_json" readonly rows="12"
               style="width:100%;box-sizing:border-box;margin-top:.5rem;font-family:monospace;font-size:.75rem"
               >${esc(x.json)}</textarea>`
          : ""
      }
      ${
        this._state.copied
          ? `<div class="status ok">${this._t(
              "share.copied",
              "Copied. Paste it into the issue, or attach the downloaded file."
            )}</div>`
          : ""
      }
    </div>`;
  }

  /** Fetch the file just written, so Copy and Download are instant. */
  async _export(deviceCode) {
    const code = deviceCode ?? this._state.saved.device_code;
    return this._call({
      type: "hub_ir/export",
      platform: this._state.platform,
      device_code: code,
    });
  }

  /** Put the JSON on the clipboard, or fall back to showing it. */
  async _copyJson() {
    const x = this._state.export;
    if (!x) return;

    try {
      await navigator.clipboard.writeText(x.json);
      this._set({ copied: true, showRaw: false });
    } catch {
      // Refused permission, or an embedding without the clipboard API. Showing
      // the text is a worse experience but never a dead end.
      this._set({
        copied: false,
        showRaw: true,
        status: {
          kind: "error",
          text: this._t(
            "err.clipboard",
            "The clipboard was refused. Select the text below and copy it."
          ),
        },
      });
    }
  }

  /** Save the file to the user's machine. A Blob, not a fetch. */
  _downloadJson(payload = this._state.export) {
    if (!payload) return;

    const url = URL.createObjectURL(
      new Blob([payload.json], { type: "application/json" })
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = payload.filename;
    // Never appended to the shadow root: the next render would tear it out.
    link.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Offer to create the entity here, rather than sending someone to a text
   * editor and a restart.
   *
   * Everything the config flow needs was settled minutes ago — the device type,
   * the code just written, the remote the codes came through — so the name is
   * the only question left, and even that has a reasonable guess. The server
   * starts the flow; this side does not need to know its steps.
   */
  _createView() {
    const s = this._state;

    return `<div class="card">
      <h2>${this._t("create.heading", "Add it to Home Assistant")}</h2>
      <p class="muted">${this._t(
        "create.sub",
        "No YAML, no restart — the entity appears straight away."
      )}</p>
      <div class="grid">
        <div>
          <label for="entity_name">${this._t("label.name", "Name")}</label>
          <input id="entity_name" value="${esc(s.entityName)}"
                 placeholder="${esc(
                   this._t("placeholder.myPlatform", "My {platform}", {
                     platform: this._platformLabel(s.platform),
                   })
                 )}" />
        </div>
        <div>
          <label for="remote">${this._t("label.remote", "Broadlink remote")}</label>
          ${this._remoteSelect()}
        </div>
      </div>
      <p class="muted" style="margin-top:.5rem">
        ${this._t(
          "create.remoteNote",
          "Already set to the remote you captured through; change it only if " +
            "this device sits in front of a different one."
        )}
      </p>
      <div class="row" style="margin-top:1rem">
        <button class="primary" id="create" ${s.creating ? "disabled" : ""}>
          ${
            s.creating
              ? this._t("button.creating", "Creating…")
              : this._t("button.create", "Create the entity")
          }
        </button>
      </div>
      ${this._statusView()}
      <details style="margin-top:1rem">
        <summary class="muted">${this._t(
          "create.yamlSummary",
          "Or write it into configuration.yaml yourself"
        )}</summary>
        <p class="muted">${this._t(
          "create.yamlNote",
          "Only worth it if you keep your entities in YAML. It needs a restart, " +
            "and the two ways of configuring one device do not know about each " +
            "other — so pick one, not both."
        )}</p>
        <pre>${esc(this._yaml())}</pre>
      </details>
    </div>`;
  }

  _createdView() {
    const s = this._state;
    const created = s.created;
    const label = created.entity_id || created.title;

    return `<div class="card">
      <h2>${
        created.existing
          ? this._t("created.already", "Already in Home Assistant")
          : this._t("created.added", "Added to Home Assistant")
      }</h2>
      <div class="status ok">
        ${
          created.existing
            ? this._t(
                "created.existingText",
                "<code>{label}</code> already uses device code {code}. It has " +
                  "been reloaded, so it is running on the file you just saved.",
                { label: esc(label), code: esc(s.saved.device_code) }
              )
            : this._t(
                "created.newText",
                "Created <code>{label}</code>. Nothing to restart.",
                { label: esc(label) }
              )
        }
      </div>
      <div class="row" style="margin-top:1rem">
        ${
          created.entity_id
            ? `<button class="primary" id="show">${this._t(
                "button.show",
                "Show it"
              )}</button>`
            : ""
        }
        <a href="/config/integrations/integration/hub_ir">${this._t(
          "link.manage",
          "Manage HubIR devices"
        )}</a>
      </div>
      <p class="muted" style="margin-top:.75rem">
        ${this._t(
          "created.renameNote",
          "Rename it, put it in an area, or point it at a different remote from " +
            "Settings &rarr; Devices &amp; services &rarr; HubIR."
        )}
      </p>
    </div>`;
  }

  /** The remote picker, shared by the setup step and the create step. */
  _remoteSelect() {
    const s = this._state;
    return `<select id="remote">
      ${s.remotes
        .map(
          (r) =>
            `<option value="${esc(r.entity_id)}"${
              r.entity_id === s.remote ? " selected" : ""
            }${r.can_learn ? "" : " disabled"}>${esc(r.name)}${
              r.can_learn
                ? ""
                : esc(this._t("remote.notBroadlink", " — not a Broadlink remote"))
            }</option>`
        )
        .join("")}
    </select>`;
  }

  /** The manual escape hatch, for people who keep their entities in YAML. */
  _yaml() {
    const s = this._state;
    const name =
      String(s.entityName || "").trim() ||
      this._t("placeholder.myPlatform", "My {platform}", {
        platform: this._platformLabel(s.platform),
      });
    return `${s.platform}:
  - platform: hub_ir
    name: ${name}
    unique_id: ${slugify(name) || `my_${s.platform}`}
    device_code: ${s.saved.device_code}
    controller_data: ${s.remote}`;
  }

  _statusView() {
    const status = this._state.status;
    if (!status) return "";
    return `<div class="status ${status.kind === "ok" ? "ok" : "error"}">${esc(
      status.text
    )}</div>`;
  }

  // -- wiring --------------------------------------------------------------

  _bind() {
    const root = this.shadowRoot;
    const on = (id, event, handler) => {
      const node = root.getElementById(id);
      if (node) node.addEventListener(event, handler);
    };

    on("platform", "change", async (event) => {
      const platform = event.target.value;
      Object.assign(this._state, {
        platform,
        spec: structuredClone(DEFAULT_SPEC[platform]),
        deviceCode: this._state.nextCode[platform],
        templateCode: "",
        customCodes: [],
        cells: [],
        codes: {},
        skipped: {},
        index: 0,
        status: null,
        entityName: "",
        created: null,
        toggle: false,
        overwrite: false,
      });
      this._render();
      await this._refreshCustomCodes();
      await this._refreshNextCode();
      this._state.deviceCode = this._state.nextCode[platform];
      this._render();
    });
    on("remote", "change", (event) => this._set({ remote: event.target.value }));
    // Changing the number withdraws any permission given for the old one.
    on("device_code", "change", (event) =>
      this._set({ deviceCode: Number(event.target.value), overwrite: false })
    );

    on("manufacturer", "input", (event) => {
      this._state.spec.manufacturer = event.target.value;
    });

    // One listener for every list control, however many lists there come to be.
    // These carry data-list; the older chips carry data-key, so the two
    // dispatches cannot collide.
    const wrap = root.querySelector(".wrap");
    if (wrap) {
      wrap.addEventListener("click", (event) => {
        const node = event.target.closest("[data-list][data-act]");
        if (node) this._listAction(node.dataset.list, node.dataset.act, node.dataset);
      });
    }

    for (const field of Object.keys(LIST_FIELDS)) {
      // No re-render on input: the add box is the one field whose caret has to
      // survive typing.
      on(`list_add_${field}`, "input", (event) => {
        this._state.draft[field] = event.target.value;
      });
      on(`list_add_${field}`, "keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        this._listAction(field, "add");
      });
    }
    for (const field of ["minTemperature", "maxTemperature", "precision"]) {
      on(field, "change", (event) =>
        this._setSpec({ [field]: Number(event.target.value) })
      );
    }
    on("temperatureUnit", "change", (event) =>
      this._setSpec({ temperatureUnit: event.target.value })
    );

    // The base state presets are captured from. Stored as one object so the
    // server receives it the same shape it writes into the device file.
    for (const [id, key] of [
      ["presetBaseMode", "operationMode"],
      ["presetBaseFanMode", "fanMode"],
      ["presetBaseTemperature", "temperature"],
    ]) {
      on(id, "change", (event) => {
        const value =
          key === "temperature" ? Number(event.target.value) : event.target.value;
        this._setSpec({
          presetBaseline: { ...this._presetBaseline(), [key]: value },
        });
      });
    }
    for (const node of root.querySelectorAll(".chip[data-key]")) {
      node.addEventListener("click", () => this._toggleChip(node.dataset.key));
    }

    for (const node of root.querySelectorAll(".chip[data-reopen]")) {
      node.addEventListener("click", () => {
        this._state.templateCode = node.dataset.reopen;
        this._loadTemplate();
      });
    }

    for (const node of root.querySelectorAll(".cell[data-index]")) {
      node.addEventListener("click", () => {
        if (this._state.running) return;
        this._set({ index: Number(node.dataset.index), status: null });
      });
    }

    on("template_code", "input", (event) => {
      this._state.templateCode = event.target.value;
    });
    on("load_template", "click", () => this._loadTemplate());

    on("plan", "click", () => this._buildPlan());
    on("run", "click", () => this._learnCurrent(true));
    on("one", "click", () => this._learnCurrent(false));
    on("stop", "click", () => this._set({ running: false }));
    on("skip", "click", () => this._skip());
    on("test", "click", () => this._test());
    on("save", "click", () => this._save());
    on("back", "click", () => this._set({ step: "setup" }));

    // Stored without re-rendering, like manufacturer and models: rebuilding the
    // shadow root on every keystroke would lose the caret.
    on("entity_name", "input", (event) => {
      this._state.entityName = event.target.value;
    });
    // change fires on blur, a safe moment to redraw the YAML fallback with the
    // name that was actually typed.
    on("entity_name", "change", () => this._render());
    on("create", "click", () => this._create());
    on("copy_json", "click", () => this._copyJson());
    on("download_json", "click", () => this._downloadJson());
    on("show_raw", "click", () =>
      this._set({ showRaw: !this._state.showRaw, copied: false })
    );

    // Download any earlier recording of your own. HACS ships only the component,
    // so a file you recorded is not in the repository to be fetched again —
    // getting it off the machine before a reinstall is the whole point.
    for (const node of root.querySelectorAll("[data-download]")) {
      node.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          this._downloadJson(await this._export(Number(node.dataset.download)));
        } catch (err) {
          this._set({ status: { kind: "error", text: this._describe(err) } });
        }
      });
    }
    on("show", "click", () => {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: this._state.created.entity_id },
          bubbles: true,
          composed: true,
        })
      );
    });
    on("restart", "click", async () => {
      Object.assign(this._state, {
        step: "setup",
        cells: [],
        codes: {},
        skipped: {},
        index: 0,
        saved: null,
        templateCode: "",
        entityName: "",
        creating: false,
        created: null,
        toggle: false,
        overwrite: false,
        // Never cleared before, so a failed create would follow the user back
        // to a fresh setup screen as a red box about nothing.
        status: null,
      });
      // The recording just saved should show up in the list straight away, and
      // the free code is a fact about the filesystem rather than the last code
      // plus one — which is what this used to guess, straight over any file the
      // user already had at that number.
      await this._refreshCustomCodes();
      await this._refreshNextCode();
      this._state.deviceCode = this._state.nextCode[this._state.platform];
      this._render();
    });
  }

  _toggleChip(key) {
    const spec = this._state.spec;
    const [kind, value] = key.split(":");

    if (kind === "mode") {
      const modes = spec.operationModes;
      spec.operationModes = modes.includes(value)
        ? modes.filter((mode) => mode !== value)
        : [...modes, value];
    } else if (kind === "button") {
      const buttons = spec.buttons;
      spec.buttons = buttons.includes(value)
        ? buttons.filter((button) => button !== value)
        : [...buttons, value];
    } else if (kind === "usesFan" || kind === "usesTemperature") {
      const options = { ...(spec.modeOptions[value] || {}) };
      options[kind] = options[kind] === false;
      spec.modeOptions = { ...spec.modeOptions, [value]: options };
    } else if (kind === "toggle" || kind === "overwrite") {
      // Session settings, not part of the spec: the final `else` writes into it.
      this._state[kind] = !this._state[kind];
    } else {
      spec[kind] = !spec[kind];
    }
    this._render();
  }
}

function chip(key, label, pressed) {
  return `<span class="chip" data-key="${esc(key)}" role="button"
    aria-pressed="${pressed ? "true" : "false"}">${esc(label)}</span>`;
}

/**
 * Normalise one typed entry, or explain why it cannot be added.
 *
 * Returns {value} or {error}. Refusing out loud is the point: the field this
 * replaced accepted "low,, high" and silently dropped the hole, and accepted
 * "High" alongside "high" as two different keys in the command tree.
 */
function listValue(raw, config, list, lang = "") {
  const text = String(raw ?? "").trim();
  const say = (key, fallback, values) =>
    fill(tr(lang, key, fallback), values || {});

  if (!text) return { error: say("err.typeSomething", "Type something first.") };
  if (text.includes("/")) {
    return {
      error: say(
        "err.noSlash",
        "A name cannot contain a slash — it would split the code's key in two."
      ),
    };
  }
  if (text.includes(",")) {
    return {
      error: say(
        "err.oneAtATime",
        "Add them one at a time; commas are not needed any more."
      ),
    };
  }
  if (text.length > MAX_NAME_LENGTH) {
    return {
      error: say("err.tooLong", "Keep it under {max} characters.", {
        max: MAX_NAME_LENGTH,
      }),
    };
  }

  if (config.numeric) {
    const number = Number(text);
    if (!Number.isFinite(number)) {
      return { error: say("err.numbersOnly", "Numbers only — 2700, not “warm”.") };
    }
    if (number <= 0) {
      return { error: say("err.positive", "Must be a positive number.") };
    }
    if (list.some((entry) => Number(entry) === number)) {
      return {
        error: say("err.duplicateNumber", "{value} is already in the list.", {
          value: number,
        }),
      };
    }
    return { value: number };
  }

  if (list.some((entry) => String(entry).toLowerCase() === text.toLowerCase())) {
    return {
      error: say("err.duplicate", "“{value}” is already in the list.", { value: text }),
    };
  }
  return { value: text };
}

/** Describe a file size the way someone deciding how to send it would read it. */
function formatBytes(bytes, lang = "") {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} ${tr(lang, "unit.bytes", "bytes")}`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function slugify(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** Guess a name from what the user already typed on the identify step. */
function defaultName(spec, platform, lang = "") {
  const model = String((spec.models || [])[0] ?? "").trim();
  const guess = [String(spec.manufacturer || "").trim(), model]
    .filter(Boolean)
    .join(" ");
  if (guess) return guess;
  return fill(tr(lang, "placeholder.myPlatform", "My {platform}"), {
    platform: tr(lang, `platform.${platform}`, PLATFORM_LABELS[platform]),
  });
}

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character]
  );
}

/**
 * Turn a rejected websocket call into a sentence.
 *
 * Only the no-details case is translated: everything else is the server's own
 * message, and those are raised in Python where this file cannot reach them.
 */
function describe(err, lang = "") {
  if (!err) return tr(lang, "err.generic", "Something went wrong.");
  return err.message || err.error || String(err);
}

customElements.define("hub-ir-panel", BroadlinkIrPanel);
