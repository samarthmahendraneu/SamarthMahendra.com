const threads = [
  {
    sender: "Maya Chen",
    subject: "Investor intro follow-up",
    preview: "Would love to continue the conversation and find 30 minutes to connect next week.",
    from: "to me",
    time: "2:18 PM",
    tag: "important",
    body: [
      "Hi,",
      "Really enjoyed meeting earlier this week. I've been thinking more about your product direction and would love to continue the conversation.",
      "If you're open to it, I'd be glad to set up a quick call sometime next week. Tuesday or Wednesday afternoon usually works well on my side.",
      "Looking forward to staying in touch.",
      "Best,",
      "Maya"
    ],
    draft: [
      "Hi Maya,",
      "Great hearing from you. I'd love to keep the conversation going.",
      "Tuesday at 3:30 PM works well on my end, so I went ahead and drafted a calendar invite. If that slot is inconvenient, feel free to suggest another time and I can adjust.",
      "Looking forward to chatting.",
      "Best,",
      "Sam"
    ]
  },
  {
    sender: "Alex Rivera",
    subject: "Draft reply for the founder update",
    preview: "Can you make this a little warmer and keep the ask for a short meeting in?",
    from: "to me",
    time: "18 min ago",
    tag: "work",
    body: [
      "Could you tighten this up and make it sound more human? I want to keep the ask for a 20-minute meeting but avoid sounding too formal."
    ],
    draft: [
      "Queued AI task",
      "Rewrite the draft with a warmer tone and preserve the scheduling ask."
    ]
  },
  {
    sender: "Jordan Kim",
    subject: "Can you send the follow-up and hold time?",
    preview: "If they're interested, propose Thursday morning and include a hold on the calendar.",
    from: "to me",
    time: "42 min ago",
    tag: "work",
    body: [
      "If this moves forward, can you send the follow-up and also place a tentative hold on Thursday morning? That'll save us a round trip."
    ],
    draft: [
      "Suggested action",
      "Draft the reply, propose Thursday 10 AM, and attach a tentative event before sending."
    ]
  },
  {
    sender: "Priya Shah",
    subject: "Quick note before the customer sync",
    preview: "Can you let them know I'll be 10 minutes late and move the invite if needed?",
    from: "to me",
    time: "1 hr ago",
    tag: "important",
    body: [
      "Running behind from the previous meeting. If possible, send a quick note and move the invite by 10 minutes."
    ],
    draft: [
      "Suggested action",
      "Draft a short delay note and update the calendar event start time."
    ]
  }
];

const labelPreviewRows = [
  ["Maya Chen", "Launch blockers grouped by impact", "important", "2:15 PM"],
  ["Jordan Kim", "Prototype shell ready for iteration", "work", "1:42 PM"],
  ["Leah Park", "Missing setup notes for contributors", "important", "12:30 PM"],
  ["Alex Rivera", "Command UI references and notes", "work", "11:30 AM"],
  ["Priya Shah", "Typography and spacing feedback", "personal", "10:15 AM"]
];

const messageList = document.getElementById("message-list");
const labelPreview = document.getElementById("label-preview");

function renderMessageList() {
  messageList.innerHTML = "";

  threads.forEach((thread, index) => {
    const item = document.createElement("button");
    item.className = `message-item${index === 0 ? " active" : ""}`;
    item.type = "button";
    item.dataset.index = String(index);
    item.innerHTML = `
      <span class="message-dot"></span>
      <span class="message-main">
        <span class="message-line">
          <strong>${thread.sender}</strong>
          <span class="tag tag-${thread.tag}">${thread.tag}</span>
        </span>
        <div class="message-subject">${thread.subject}</div>
        <div class="message-preview">${thread.preview}</div>
      </span>
      <span class="muted mono">${thread.time}</span>
    `;

    item.addEventListener("click", () => selectThread(index));
    messageList.appendChild(item);
  });
}

function renderLabelPreview() {
  labelPreview.innerHTML = "";

  labelPreviewRows.forEach(([sender, subject, tag, time]) => {
    const row = document.createElement("div");
    row.className = "label-row";
    row.innerHTML = `
      <span class="message-dot"></span>
      <strong>${sender}</strong>
      <span>${subject}</span>
      <span class="tag tag-${tag}">${tag}</span>
      <span class="muted mono">${time}</span>
    `;
    labelPreview.appendChild(row);
  });
}

function selectThread(index) {
  const thread = threads[index];

  document.querySelectorAll(".message-item").forEach((node, nodeIndex) => {
    node.classList.toggle("active", nodeIndex === index);
  });

  document.getElementById("thread-subject").textContent = thread.subject;
  document.getElementById("thread-sender").textContent = thread.sender;
  document.getElementById("thread-from").textContent = thread.from;
  document.getElementById("thread-time").textContent = thread.time;
  document.getElementById("thread-body").innerHTML = thread.body.map((paragraph) => `<p>${paragraph}</p>`).join("");
  document.getElementById("draft-body").innerHTML = thread.draft.map((paragraph) => `<p>${paragraph}</p>`).join("");
}

renderMessageList();
renderLabelPreview();
selectThread(0);
