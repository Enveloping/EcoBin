import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  compareMoneyCny as compareMiniMoney,
  isMoneyInputDraft as isMiniMoneyDraft,
  normalizeMoneyInput as normalizeMiniMoney,
} from '../miniprogram/miniprogram/utils/decimal.ts';
import {
  isMoneyInputDraft as isWebMoneyDraft,
  normalizeMoneyInput as normalizeWebMoney,
  parseMoneyInputCent,
} from '../web/src/utils/decimal.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('miniapp money input accepts zero to two decimals without rounding', () => {
  assert.equal(normalizeMiniMoney('4'), '4.00');
  assert.equal(normalizeMiniMoney('0.4'), '0.40');
  assert.equal(normalizeMiniMoney('4.'), '4.00');
  assert.equal(normalizeMiniMoney('4.56'), '4.56');
  assert.equal(normalizeMiniMoney('4.567'), null);
  assert.equal(normalizeMiniMoney('.4'), null);
  assert.equal(normalizeMiniMoney('-4'), null);
  assert.equal(isMiniMoneyDraft('4.56'), true);
  assert.equal(isMiniMoneyDraft('4.567'), false);
});

test('miniapp compares canonical amounts against the backend balance exactly', () => {
  const amount = normalizeMiniMoney('4');
  assert.ok(amount);
  assert.equal(compareMiniMoney(amount, '4.00'), 0);
  assert.equal(compareMiniMoney(amount, '3.99'), 1);
  assert.equal(compareMiniMoney(amount, '4.01'), -1);
});

test('web recharge and signed wallet adjustment share friendly input rules', () => {
  assert.equal(normalizeWebMoney('4'), '4.00');
  assert.equal(normalizeWebMoney('0.4'), '0.40');
  assert.equal(normalizeWebMoney('4.567'), null);
  assert.equal(normalizeWebMoney('-4'), null);
  assert.equal(normalizeWebMoney('-4', true), '-4.00');
  assert.equal(normalizeWebMoney('-0.4', true), '-0.40');
  assert.equal(parseMoneyInputCent('4', true), 400n);
  assert.equal(parseMoneyInputCent('-0.4', true), -40n);
  assert.equal(isWebMoneyDraft('-', true), true);
  assert.equal(isWebMoneyDraft('-0.456', true), false);
});

test('withdrawal page keeps backend authority and never treats the WeChat page as success', () => {
  const validation = source(
    '../miniprogram/miniprogram/utils/withdrawal-validation.ts',
  );
  const page = source(
    '../miniprogram/miniprogram/pages/withdrawals/withdrawals.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/withdrawals/withdrawals.wxml',
  );
  const styles = source(
    '../miniprogram/miniprogram/pages/withdrawals/withdrawals.wxss',
  );

  assert.match(
    validation,
    /compareMoneyCny\(amountYuan, availableBalanceYuan\) > 0/,
  );
  assert.match(validation, /BALANCE_INSUFFICIENT/);
  assert.match(page, /createIntent: null as PendingWithdrawalIntent \| null/);
  assert.match(page, /const validation = validateWithdrawalAmount\(/);
  assert.match(page, /validateWithdrawalAmount/);
  assert.match(page, /requestMerchantTransfer/);
  assert.match(page, /myWithdrawal\(withdrawalNo, false\)/);
  assert.match(page, /TERMINAL_STATUSES\.has\(detail\.status\)/);
  assert.doesNotMatch(page, /parseFloat|toFixed/);
  assert.doesNotMatch(markup, /取消提现|cancelWithdrawal/);
  assert.ok(markup.includes(
    'wx:if="{{!authorizationLoading && (!authorization || authorization.status !== \'ACTIVE\')}}"',
  ));
  assert.match(markup, /wx:if="\{\{amountError\}\}" class="amount-error"/);
  assert.match(markup, /class="amount-balance"/);
  assert.ok(
    markup.indexOf('class="amount-box') < markup.indexOf('class="amount-balance"')
      && markup.indexOf('class="amount-balance"') < markup.indexOf('class="submit-button'),
  );
  assert.doesNotMatch(markup, /class="balance-panel"|可用余额/);
  assert.match(markup, /background-shape background-shape--mint/);
  assert.match(markup, /background-shape background-shape--cream/);
  assert.match(styles, /\.background-shape--mint/);
  assert.match(styles, /\.background-shape--cream/);
  assert.doesNotMatch(markup, /create-card-accent/);
  assert.doesNotMatch(styles, /\.create-card-accent/);
  assert.match(
    styles,
    /\.amount-balance-label\s*\{[^}]*font-size:\s*24rpx;/s,
  );
  assert.match(
    styles,
    /\.amount-balance-value\s*\{[^}]*font-size:\s*32rpx;/s,
  );
  assert.match(styles, /\.submit-button\s*\{[^}]*background:\s*#2b7f45;/s);
  assert.match(
    styles,
    /\.create-card button\.submit-button\[disabled\]\s*\{[^}]*background:\s*#dce2dc;[^}]*opacity:\s*1;/s,
  );
  assert.doesNotMatch(
    markup,
    /MANUAL WITHDRAWAL|已开通|单笔提现范围|最多两位小数|提交后不可由用户取消|最终结果以后端查单状态为准|item\.sourceText|来源投递单/,
  );
});

test('funds clients normalize money before building command payloads', () => {
  const rechargeCard = source(
    '../web/src/pages/funds/OrganizationRechargeCard.tsx',
  );
  const fundsPage = source('../web/src/pages/funds/index.tsx');
  const usersPage = source('../web/src/pages/organization-user/index.tsx');
  const openapi = source('../../contracts/http/openapi.yaml');

  assert.match(rechargeCard, /grossAmountYuan: amountSnapshot/);
  assert.match(fundsPage, /status: 'POSTED'/);
  assert.match(usersPage, /deltaYuan: normalizedDelta/);
  assert.match(
    openapi,
    /\/api\/v1\/miniapp\/me\/withdrawal-configuration/,
  );
});
