// components/city-picker/city-picker.js
/**
 * 城市选择器组件：热门城市 + 全国城市字母索引 + 搜索过滤
 * 用法：
 *   <city-picker show="{{show}}" city="{{city}}" bindselect="onCitySelect" bindclose="onClose" />
 */
const { HOT_CITIES, CITY_GROUPS } = require('../../utils/cities')

Component({
  properties: {
    show: { type: Boolean, value: false },
    city: { type: String, value: '' }
  },

  data: {
    hotCities: HOT_CITIES,
    cityGroups: CITY_GROUPS,
    keyword: '',
    searchEmpty: false
  },

  observers: {
    keyword(kw) {
      const k = (kw || '').trim()
      if (!k) {
        this.setData({ cityGroups: CITY_GROUPS, searchEmpty: false })
        return
      }
      const groups = CITY_GROUPS.map((g) => ({
        letter: g.letter,
        list: g.list.filter((c) => c.indexOf(k) > -1)
      })).filter((g) => g.list.length)
      this.setData({ cityGroups: groups, searchEmpty: groups.length === 0 })
    }
  },

  methods: {
    onSearchInput(e) {
      this.setData({ keyword: e.detail.value || '' })
    },
    onCityTap(e) {
      const city = e.currentTarget.dataset.city
      if (city) this.triggerEvent('select', { city })
    },
    onClose() {
      this.triggerEvent('close')
    },
    noop() {
      /* 阻止冒泡 */
    }
  }
})
