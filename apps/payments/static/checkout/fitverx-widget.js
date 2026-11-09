(function () {
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return '';
  }

  function getQueryParam(param) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(param) || '';
  }

  function initWidget() {
    const container = document.getElementById('fitverx-checkout-widget');
    if (!container) return;

    const tenantId = container.getAttribute('data-tenant-id');
    if (!tenantId) {
      container.innerHTML = '<p style="color:red;">Fitverx Widget: Missing data-tenant-id attribute.</p>';
      return;
    }

    const scriptTag = document.currentScript || document.querySelector('script[src*="fitverx-widget.js"]');
    let apiBase = '';
    if (scriptTag && scriptTag.src) {
      const url = new URL(scriptTag.src);
      apiBase = `${url.protocol}//${url.host}`;
    }

    const shadow = container.attachShadow({ mode: 'open' });

    const style = document.createElement('style');
    style.textContent = `
      :host {
        display: block;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: #111827;
      }
      .fitverx-container {
        width: 100%;
        max-width: 1100px;
        margin: 0 auto;
        padding: 16px 0;
      }
      .fitverx-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 20px;
        margin-top: 20px;
      }
      .fitverx-card {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 24px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        transition: transform 0.2s, box-shadow 0.2s;
      }
      .fitverx-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0,0,0,0.08);
      }
      .fitverx-card-title {
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 6px;
      }
      .fitverx-card-location {
        font-size: 13px;
        color: #6b7280;
        margin-bottom: 12px;
      }
      .fitverx-card-desc {
        font-size: 14px;
        color: #4b5563;
        line-height: 1.4;
        margin-bottom: 16px;
        flex-grow: 1;
      }
      .fitverx-card-price {
        font-size: 26px;
        font-weight: 800;
        margin-bottom: 16px;
      }
      .fitverx-card-price span {
        font-size: 14px;
        color: #6b7280;
        font-weight: 500;
      }
      .fitverx-btn {
        display: block;
        width: 100%;
        padding: 12px;
        border-radius: 8px;
        border: none;
        font-size: 15px;
        font-weight: 600;
        text-align: center;
        cursor: pointer;
        color: #ffffff;
        box-sizing: border-box;
      }
      .fitverx-modal-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
        z-index: 999999;
      }
      .fitverx-modal {
        background: #ffffff;
        border-radius: 14px;
        width: 100%;
        max-width: 440px;
        padding: 28px;
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
        position: relative;
      }
      .fitverx-close {
        position: absolute;
        top: 16px;
        right: 16px;
        background: none;
        border: none;
        font-size: 22px;
        cursor: pointer;
        color: #9ca3af;
      }
      .fitverx-form-group {
        margin-bottom: 16px;
      }
      .fitverx-label {
        display: block;
        font-size: 13px;
        font-weight: 600;
        margin-bottom: 6px;
      }
      .fitverx-input {
        width: 100%;
        padding: 10px 12px;
        border: 1px solid #d1d5db;
        border-radius: 8px;
        font-size: 15px;
        box-sizing: border-box;
      }
      .fitverx-error {
        color: #dc2626;
        font-size: 13px;
        margin-bottom: 12px;
      }
      .fitverx-otp-input {
        letter-spacing: 8px;
        font-size: 24px;
        font-weight: 700;
        text-align: center;
      }
    `;
    shadow.appendChild(style);

    const root = document.createElement('div');
    root.className = 'fitverx-container';
    root.innerHTML = '<p>Loading available membership plans...</p>';
    shadow.appendChild(root);

    let tenantConfig = null;
    let selectedPackage = null;
    let currentChallengeId = null;
    let customerData = {};

    fetch(`${apiBase}/api/v1/checkout/public/tenants/${tenantId}/packages/`, {
      headers: { 'Origin': window.location.origin }
    })
    .then(res => {
      if (!res.ok) throw new Error('Checkout unavailable for this gym.');
      return res.json();
    })
    .then(data => {
      tenantConfig = data.tenant;
      renderPackages(data.packages);
    })
    .catch(err => {
      root.innerHTML = `<p style="color:#6b7280; font-size:14px;">${err.message}</p>`;
    });

    function renderPackages(packages) {
      if (!packages || packages.length === 0) {
        root.innerHTML = '<p>No membership plans are currently open for online enrollment.</p>';
        return;
      }

      const primaryColor = tenantConfig.theme_primary_color || '#111827';
      const buttonText = tenantConfig.widget_button_text || 'Select Plan';

      let html = '<div class="fitverx-grid">';
      packages.forEach(pkg => {
        html += `
          <div class="fitverx-card" data-pkg-id="${pkg.id}">
            <div>
              <div class="fitverx-card-title">${pkg.name}</div>
              ${pkg.location_name ? `<div class="fitverx-card-location">${pkg.location_name}</div>` : ''}
              <div class="fitverx-card-desc">${pkg.description || `${pkg.credit_count} credits valid for ${pkg.validity_days} days.`}</div>
            </div>
            <div>
              <div class="fitverx-card-price">$${pkg.price} <span>/ ${pkg.billing_cycle}</span></div>
              <button class="fitverx-btn" style="background:${primaryColor};" data-package-id="${pkg.id}">${buttonText}</button>
            </div>
          </div>
        `;
      });
      html += '</div>';
      root.innerHTML = html;

      root.querySelectorAll('.fitverx-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const pkgId = e.target.getAttribute('data-package-id');
          selectedPackage = packages.find(p => p.id === pkgId);
          openEmailModal();
        });
      });
    }

    function openEmailModal() {
      const modalOverlay = document.createElement('div');
      modalOverlay.className = 'fitverx-modal-overlay';
      modalOverlay.innerHTML = `
        <div class="fitverx-modal">
          <button class="fitverx-close">&times;</button>
          <h3 style="margin-top:0; margin-bottom:6px; font-size:18px;">${selectedPackage.name}</h3>
          <p style="margin:0 0 16px 0; font-size:14px; color:#4b5563;">Enter your email to verify your membership purchase.</p>
          <div id="fitverx-step-1">
            <div id="fitverx-email-err" class="fitverx-error" style="display:none;"></div>
            <div class="fitverx-form-group">
              <label class="fitverx-label">Email Address *</label>
              <input type="email" id="fitverx-input-email" class="fitverx-input" placeholder="you@example.com" required>
            </div>
            <div class="fitverx-form-group" style="display:flex; gap:10px;">
              <div style="flex:1;">
                <label class="fitverx-label">First Name</label>
                <input type="text" id="fitverx-input-first-name" class="fitverx-input" placeholder="Alex">
              </div>
              <div style="flex:1;">
                <label class="fitverx-label">Last Name</label>
                <input type="text" id="fitverx-input-last-name" class="fitverx-input" placeholder="Smith">
              </div>
            </div>
            ${tenantConfig.require_phone_number ? `
              <div class="fitverx-form-group">
                <label class="fitverx-label">Phone Number *</label>
                <input type="tel" id="fitverx-input-phone" class="fitverx-input" placeholder="+1 555-0199" required>
              </div>
            ` : ''}
            <button id="fitverx-send-otp-btn" class="fitverx-btn" style="background:${tenantConfig.theme_primary_color || '#111827'};">
              Continue
            </button>
          </div>
          <div id="fitverx-step-2" style="display:none;">
            <div id="fitverx-otp-err" class="fitverx-error" style="display:none;"></div>
            <div class="fitverx-form-group">
              <label class="fitverx-label">Enter 6-digit Code sent to your email</label>
              <input type="text" id="fitverx-input-otp" class="fitverx-input fitverx-otp-input" maxlength="6" placeholder="000000">
            </div>
            <button id="fitverx-verify-otp-btn" class="fitverx-btn" style="background:${tenantConfig.theme_primary_color || '#111827'};">
              Verify and Proceed to Checkout
            </button>
          </div>
        </div>
      `;

      shadow.appendChild(modalOverlay);

      modalOverlay.querySelector('.fitverx-close').addEventListener('click', () => {
        shadow.removeChild(modalOverlay);
      });

      const sendBtn = modalOverlay.querySelector('#fitverx-send-otp-btn');
      sendBtn.addEventListener('click', () => {
        const emailInput = modalOverlay.querySelector('#fitverx-input-email');
        const emailErr = modalOverlay.querySelector('#fitverx-email-err');
        const email = emailInput.value.trim();

        if (!email || !email.includes('@')) {
          emailErr.textContent = 'Please enter a valid email address.';
          emailErr.style.display = 'block';
          return;
        }

        customerData = {
          email: email,
          first_name: (modalOverlay.querySelector('#fitverx-input-first-name')?.value || '').trim(),
          last_name: (modalOverlay.querySelector('#fitverx-input-last-name')?.value || '').trim(),
          phone_number: (modalOverlay.querySelector('#fitverx-input-phone')?.value || '').trim(),
        };

        sendBtn.disabled = true;
        sendBtn.textContent = 'Sending code...';
        emailErr.style.display = 'none';

        fetch(`${apiBase}/api/v1/checkout/public/otp/send/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin
          },
          body: JSON.stringify({
            tenant_id: tenantId,
            package_type_id: selectedPackage.id,
            email: customerData.email
          })
        })
        .then(res => res.json().then(data => ({ status: res.status, body: data })))
        .then(({ status, body }) => {
          sendBtn.disabled = false;
          sendBtn.textContent = 'Continue';
          if (status !== 200) {
            emailErr.textContent = body.detail || body.email || 'Unable to send verification code.';
            emailErr.style.display = 'block';
            return;
          }
          currentChallengeId = body.challenge_id;
          modalOverlay.querySelector('#fitverx-step-1').style.display = 'none';
          modalOverlay.querySelector('#fitverx-step-2').style.display = 'block';
        })
        .catch(() => {
          sendBtn.disabled = false;
          sendBtn.textContent = 'Continue';
          emailErr.textContent = 'Network error. Please try again.';
          emailErr.style.display = 'block';
        });
      });

      const verifyBtn = modalOverlay.querySelector('#fitverx-verify-otp-btn');
      verifyBtn.addEventListener('click', () => {
        const otpInput = modalOverlay.querySelector('#fitverx-input-otp');
        const otpErr = modalOverlay.querySelector('#fitverx-otp-err');
        const otpCode = otpInput.value.trim();

        if (otpCode.length !== 6) {
          otpErr.textContent = 'Please enter the 6-digit verification code.';
          otpErr.style.display = 'block';
          return;
        }

        verifyBtn.disabled = true;
        verifyBtn.textContent = 'Verifying...';
        otpErr.style.display = 'none';

        const fbp = getCookie('_fbp');
        let fbc = getCookie('_fbc');
        const fbclid = getQueryParam('fbclid');
        if (!fbc && fbclid) {
          fbc = `fb.1.${Date.now()}.${fbclid}`;
        }

        fetch(`${apiBase}/api/v1/checkout/public/otp/verify/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin
          },
          body: JSON.stringify({
            challenge_id: currentChallengeId,
            tenant_id: tenantId,
            package_type_id: selectedPackage.id,
            email: customerData.email,
            otp_code: otpCode,
            first_name: customerData.first_name,
            last_name: customerData.last_name,
            phone_number: customerData.phone_number,
            fbp: fbp,
            fbc: fbc,
            source_url: window.location.href
          })
        })
        .then(res => res.json().then(data => ({ status: res.status, body: data })))
        .then(({ status, body }) => {
          verifyBtn.disabled = false;
          verifyBtn.textContent = 'Verify and Proceed to Checkout';
          if (status !== 201) {
            otpErr.textContent = body.detail || body.otp_code || 'Verification failed.';
            otpErr.style.display = 'block';
            return;
          }
          window.location.href = `${apiBase}${body.checkout_url}`;
        })
        .catch(() => {
          verifyBtn.disabled = false;
          verifyBtn.textContent = 'Verify and Proceed to Checkout';
          otpErr.textContent = 'Network error. Please try again.';
          otpErr.style.display = 'block';
        });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWidget);
  } else {
    initWidget();
  }
})();

