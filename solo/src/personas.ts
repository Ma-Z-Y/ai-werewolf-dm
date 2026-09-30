import type { Look } from './render/pixel';

/** An AI townsperson: name + personality (for the prompt) + a look that matches their trade. */
export interface Persona {
  name: string;
  trait: string;
  look: Look;
}

const SKIN = { fair: '#e8c3a2', light: '#e0b48f', tan: '#c99873', brown: '#a8775a', pale: '#d8c4b4' };

export const PERSONAS: Persona[] = [
  {
    name: '铁匠艾德',
    trait: '说话直来直去，脾气急',
    look: { skin: SKIN.tan, hair: '#1e1612', hairStyle: 'short', beard: 'full', head: 'none', body: 'apron', main: '#4a4a52', dark: '#303036', accent: '#6a4428', pants: '#2c2a2e' },
  },
  {
    name: '修女玛莎',
    trait: '温和谨慎，爱讲道理',
    look: { skin: SKIN.fair, hair: '#1a1a1e', hairStyle: 'short', beard: 'none', head: 'wimple', headColor: '#16161c', body: 'habit', main: '#1e1e26', dark: '#121218', accent: '#e8e4dc', pants: '#1e1e26' },
  },
  {
    name: '酒馆老板布兰',
    trait: '圆滑健谈，喜欢开玩笑',
    look: { skin: SKIN.light, hair: '#9a4a24', hairStyle: 'bald', beard: 'mustache', head: 'none', body: 'apron', main: '#743434', dark: '#4a2020', accent: '#d8d0c0', pants: '#3a3028' },
  },
  {
    name: '猎户罗根',
    trait: '沉默寡言，一句话切中要害',
    look: { skin: SKIN.tan, hair: '#3a2a1e', hairStyle: 'short', beard: 'stubble', head: 'hood', headColor: '#3e5230', body: 'cloak', main: '#3e5230', dark: '#28361e', accent: '#6a5a3a', pants: '#4a3c2c' },
  },
  {
    name: '学徒莉娜',
    trait: '年轻紧张，有时会自我怀疑',
    look: { skin: SKIN.fair, hair: '#8a4a26', hairStyle: 'braid', beard: 'none', head: 'none', body: 'dress', main: '#3a5a7a', dark: '#26405a', accent: '#d8d0c0', pants: '#3a5a7a' },
  },
  {
    name: '老农托马斯',
    trait: '慢条斯理，爱用农谚打比方',
    look: { skin: SKIN.brown, hair: '#b8b4ac', hairStyle: 'short', beard: 'full', beardColor: '#c8c4bc', head: 'strawHat', body: 'tunic', main: '#7a6a48', dark: '#54482e', accent: '#7a6a48', pants: '#4a3c2c' },
  },
  {
    name: '吟游诗人菲恩',
    trait: '语言华丽，喜欢夸张',
    look: { skin: SKIN.light, hair: '#d8b060', hairStyle: 'long', beard: 'none', head: 'featherCap', headColor: '#6a2a6a', body: 'tunic', main: '#7a3a7a', dark: '#4e224e', accent: '#d8b040', pants: '#c8a040' },
  },
  {
    name: '药师伊索',
    trait: '冷静理性，注重逻辑链',
    look: { skin: SKIN.fair, hair: '#2a2420', hairStyle: 'short', beard: 'mustache', head: 'none', body: 'robe', main: '#2e5a5a', dark: '#1e3c3c', accent: '#b8a060', pants: '#2e5a5a' },
  },
  {
    name: '守墓人格里姆',
    trait: '阴沉多疑，怀疑一切',
    look: { skin: SKIN.pale, hair: '#6a6660', hairStyle: 'short', beard: 'stubble', head: 'hood', headColor: '#2a2a30', body: 'cloak', main: '#2a2a30', dark: '#18181c', accent: '#4a4038', pants: '#24242a' },
  },
  {
    name: '裁缝薇拉',
    trait: '观察细致，关注别人措辞',
    look: { skin: SKIN.fair, hair: '#1e1614', hairStyle: 'bun', beard: 'none', head: 'none', body: 'dress', main: '#7a2e3e', dark: '#521e2a', accent: '#d8c070', pants: '#7a2e3e' },
  },
  {
    name: '磨坊主汉斯',
    trait: '热心肠，容易被说服',
    look: { skin: SKIN.light, hair: '#8a6a3a', hairStyle: 'short', beard: 'mustache', head: 'cap', headColor: '#d8d4c8', body: 'apron', main: '#6a5238', dark: '#46362a', accent: '#e4e0d4', pants: '#4a3c2c' },
  },
  {
    name: '流浪骑士卡尔',
    trait: '自信强势，喜欢带节奏',
    look: { skin: SKIN.tan, hair: '#3a2a1e', hairStyle: 'short', beard: 'stubble', head: 'helmet', body: 'armor', main: '#8a8e96', dark: '#5e626a', accent: '#8a2a24', pants: '#3a3a40' },
  },
];

/** The human player: a hooded traveller who just arrived in town. */
export const TRAVELLER_LOOK: Look = {
  skin: SKIN.light, hair: '#4a3222', hairStyle: 'short', beard: 'none', head: 'hood', headColor: '#6a4a30',
  body: 'cloak', main: '#6a4a30', dark: '#46301e', accent: '#8a7a5a', pants: '#3a3028',
};
