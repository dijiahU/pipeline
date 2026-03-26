description = "Pipeline API Key"
admin_email = ENV.fetch("DISCOURSE_ADMIN_EMAIL")
admin = User.find_by(username_lower: "admin")
admin ||= (UserEmail.find_by(email: admin_email) || UserEmail.find_by(normalized_email: admin_email.downcase))&.user
raise "admin user missing" unless admin

api_key = ApiKey.where(description: description).order(:id).last

unless api_key
  if ApiKey.respond_to?(:create_master_key)
    created = ApiKey.create_master_key(description: description)
    api_key = created if created.is_a?(ApiKey)
    api_key ||= ApiKey.where(description: description).order(:id).last
  end
end

unless api_key
  api_key = ApiKey.new(description: description)
  api_key.user = admin if api_key.respond_to?(:user=)
  api_key.created_by = admin if api_key.respond_to?(:created_by=)
  api_key.save!
end

key_value = api_key.respond_to?(:key) ? api_key.key : api_key.to_s
raise "api key generation failed" if key_value.nil? || key_value.empty?

puts key_value
